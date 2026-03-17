from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text
import uuid, json, os

from main import Trip, Expense

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data.db")
# Railway provides postgres:// but SQLAlchemy needs postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)

app = FastAPI(title="Splitting Fares")


# ── Request schemas ────────────────────────────────────────────────────────────

class CreateTrip(BaseModel):
    name: str

class AddMember(BaseModel):
    name: str

class AddExpense(BaseModel):
    description: str
    amount: float
    paid_by: str
    split_among: list[str]


# ── DB init ────────────────────────────────────────────────────────────────────

@app.on_event("startup")
def init_db():
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS trips (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                data TEXT NOT NULL
            )
        """))
        conn.commit()


# ── Storage helpers ────────────────────────────────────────────────────────────

def _row_to_trip(raw_json: str) -> Trip:
    raw = json.loads(raw_json)
    trip = Trip(name=raw["name"], members=raw["members"])
    trip.expenses = [
        Expense(e["description"], e["amount"], e["paid_by"], e["split_among"])
        for e in raw["expenses"]
    ]
    return trip

def _trip_to_json(trip: Trip) -> str:
    return json.dumps({
        "name": trip.name,
        "members": trip.members,
        "expenses": [
            {"description": e.description, "amount": e.amount,
             "paid_by": e.paid_by, "split_among": e.split_among}
            for e in trip.expenses
        ],
    })

def db_load_all() -> dict[str, Trip]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, data FROM trips ORDER BY rowid")).fetchall()
    return {row[0]: _row_to_trip(row[1]) for row in rows}

def db_load(trip_id: str) -> Trip | None:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT data FROM trips WHERE id = :id"), {"id": trip_id}).fetchone()
    return _row_to_trip(row[0]) if row else None

def db_save(trip_id: str, trip: Trip):
    data = _trip_to_json(trip)
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM trips WHERE id = :id"), {"id": trip_id}).fetchone()
        if exists:
            conn.execute(text("UPDATE trips SET name = :name, data = :data WHERE id = :id"),
                         {"id": trip_id, "name": trip.name, "data": data})
        else:
            conn.execute(text("INSERT INTO trips (id, name, data) VALUES (:id, :name, :data)"),
                         {"id": trip_id, "name": trip.name, "data": data})
        conn.commit()

def db_delete(trip_id: str):
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM trips WHERE id = :id"), {"id": trip_id})
        conn.commit()

def get_trip_or_404(trip_id: str) -> Trip:
    trip = db_load(trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip

def trip_to_dict(trip_id: str, trip: Trip) -> dict:
    balances = trip.balances()
    settlements = trip.settlements()
    return {
        "id": trip_id,
        "name": trip.name,
        "members": trip.members,
        "expenses": [
            {"description": e.description, "amount": e.amount,
             "paid_by": e.paid_by, "split_among": e.split_among}
            for e in trip.expenses
        ],
        "balances": [
            {"person": p, "amount": round(b, 2),
             "status": "owed" if b > 0.005 else ("owes" if b < -0.005 else "settled"),
             "total_paid": round(sum(e.amount for e in trip.expenses if e.paid_by == p), 2)}
            for p, b in balances.items()
        ],
        "settlements": [
            {"from": debtor, "to": creditor, "amount": amount}
            for debtor, creditor, amount in settlements
        ],
    }


# ── API routes ─────────────────────────────────────────────────────────────────

@app.post("/api/trips")
def create_trip(body: CreateTrip):
    trip_id = str(uuid.uuid4())[:8]
    trip = Trip(name=body.name)
    db_save(trip_id, trip)
    return trip_to_dict(trip_id, trip)

@app.get("/api/trips")
def list_trips():
    return [trip_to_dict(tid, t) for tid, t in db_load_all().items()]

@app.get("/api/trips/{trip_id}")
def get_trip_detail(trip_id: str):
    return trip_to_dict(trip_id, get_trip_or_404(trip_id))

@app.delete("/api/trips/{trip_id}")
def delete_trip(trip_id: str):
    get_trip_or_404(trip_id)
    db_delete(trip_id)
    return {"ok": True}

@app.post("/api/trips/{trip_id}/members")
def add_member(trip_id: str, body: AddMember):
    trip = get_trip_or_404(trip_id)
    if body.name in trip.members:
        raise HTTPException(status_code=400, detail=f"{body.name} is already on the trip")
    trip.add_member(body.name)
    db_save(trip_id, trip)
    return trip_to_dict(trip_id, trip)

@app.delete("/api/trips/{trip_id}/members/{member_name}")
def delete_member(trip_id: str, member_name: str):
    trip = get_trip_or_404(trip_id)
    if member_name not in trip.members:
        raise HTTPException(status_code=404, detail=f"{member_name} is not on the trip")
    involved = [e for e in trip.expenses if e.paid_by == member_name or member_name in e.split_among]
    if involved:
        raise HTTPException(status_code=400, detail=f"Cannot remove {member_name} — they have expenses. Delete those first.")
    trip.members.remove(member_name)
    db_save(trip_id, trip)
    return trip_to_dict(trip_id, trip)

@app.post("/api/trips/{trip_id}/expenses")
def add_expense(trip_id: str, body: AddExpense):
    trip = get_trip_or_404(trip_id)
    if body.paid_by not in trip.members:
        raise HTTPException(status_code=400, detail=f"{body.paid_by} is not on the trip")
    invalid = [p for p in body.split_among if p not in trip.members]
    if invalid:
        raise HTTPException(status_code=400, detail=f"{', '.join(invalid)} are not on the trip")
    if not body.split_among:
        raise HTTPException(status_code=400, detail="Must split among at least one person")
    trip.add_expense(body.description, body.amount, body.paid_by, body.split_among)
    db_save(trip_id, trip)
    return trip_to_dict(trip_id, trip)

@app.delete("/api/trips/{trip_id}/expenses/{expense_index}")
def delete_expense(trip_id: str, expense_index: int):
    trip = get_trip_or_404(trip_id)
    if expense_index < 0 or expense_index >= len(trip.expenses):
        raise HTTPException(status_code=404, detail="Expense not found")
    trip.expenses.pop(expense_index)
    db_save(trip_id, trip)
    return trip_to_dict(trip_id, trip)


# ── Serve frontend ─────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def index():
    return FileResponse("static/index.html")
