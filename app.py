from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, text
import uuid, json, os
from collections import defaultdict

from main import Trip, Expense

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data.db")
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

class AddPayment(BaseModel):
    sent_by: str
    sent_to: str
    amount: float
    note: str = ""


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

def _row_to_data(raw_json: str) -> tuple[Trip, list]:
    raw = json.loads(raw_json)
    trip = Trip(name=raw["name"], members=raw["members"])
    trip.expenses = [
        Expense(e["description"], e["amount"], e["paid_by"], e["split_among"])
        for e in raw["expenses"]
    ]
    payments = raw.get("payments", [])
    return trip, payments

def _data_to_json(trip: Trip, payments: list) -> str:
    return json.dumps({
        "name": trip.name,
        "members": trip.members,
        "expenses": [
            {"description": e.description, "amount": e.amount,
             "paid_by": e.paid_by, "split_among": e.split_among}
            for e in trip.expenses
        ],
        "payments": payments,
    })

def db_load_all() -> list[tuple[str, Trip, list]]:
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT id, data FROM trips ORDER BY rowid")).fetchall()
    return [(row[0], *_row_to_data(row[1])) for row in rows]

def db_load(trip_id: str) -> tuple[Trip, list] | None:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT data FROM trips WHERE id = :id"), {"id": trip_id}).fetchone()
    return _row_to_data(row[0]) if row else None

def db_save(trip_id: str, trip: Trip, payments: list):
    data = _data_to_json(trip, payments)
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

def get_data_or_404(trip_id: str) -> tuple[Trip, list]:
    result = db_load(trip_id)
    if not result:
        raise HTTPException(status_code=404, detail="Trip not found")
    return result


# ── Balance helpers ────────────────────────────────────────────────────────────

def adjusted_balances(trip: Trip, payments: list) -> dict[str, float]:
    """Expense-based balances minus any Zelle/Venmo payments already made."""
    bal = defaultdict(float, trip.balances())
    for p in payments:
        bal[p["sent_by"]] += p["amount"]   # sender paid out → their debt shrinks
        bal[p["sent_to"]] -= p["amount"]   # receiver got money → their credit shrinks
    return dict(bal)

def calc_settlements(balances: dict) -> list[tuple[str, str, float]]:
    debtors  = sorted([(p, -b) for p, b in balances.items() if b < -0.005], key=lambda x: x[1], reverse=True)
    creditors = sorted([(p, b) for p, b in balances.items() if b > 0.005],  key=lambda x: x[1], reverse=True)
    debtors   = [[p, a] for p, a in debtors]
    creditors = [[p, a] for p, a in creditors]
    result, i, j = [], 0, 0
    while i < len(debtors) and j < len(creditors):
        debtor, owe = debtors[i]
        creditor, get = creditors[j]
        transfer = min(owe, get)
        result.append((debtor, creditor, round(transfer, 2)))
        debtors[i][1] -= transfer
        creditors[j][1] -= transfer
        if debtors[i][1] < 0.005: i += 1
        if creditors[j][1] < 0.005: j += 1
    return result


# ── Response builder ───────────────────────────────────────────────────────────

def trip_to_dict(trip_id: str, trip: Trip, payments: list) -> dict:
    bal = adjusted_balances(trip, payments)
    return {
        "id": trip_id,
        "name": trip.name,
        "members": trip.members,
        "expenses": [
            {"description": e.description, "amount": e.amount,
             "paid_by": e.paid_by, "split_among": e.split_among}
            for e in trip.expenses
        ],
        "payments": payments,
        "balances": [
            {"person": p, "amount": round(b, 2),
             "status": "owed" if b > 0.005 else ("owes" if b < -0.005 else "settled"),
             "total_paid": round(sum(e.amount for e in trip.expenses if e.paid_by == p), 2)}
            for p, b in bal.items()
        ],
        "settlements": [
            {"from": debtor, "to": creditor, "amount": amount}
            for debtor, creditor, amount in calc_settlements(bal)
        ],
    }


# ── API routes ─────────────────────────────────────────────────────────────────

@app.post("/api/trips")
def create_trip(body: CreateTrip):
    trip_id = str(uuid.uuid4())[:8]
    trip = Trip(name=body.name)
    db_save(trip_id, trip, [])
    return trip_to_dict(trip_id, trip, [])

@app.get("/api/trips")
def list_trips():
    return [trip_to_dict(tid, t, p) for tid, t, p in db_load_all()]

@app.get("/api/trips/{trip_id}")
def get_trip_detail(trip_id: str):
    trip, payments = get_data_or_404(trip_id)
    return trip_to_dict(trip_id, trip, payments)

@app.delete("/api/trips/{trip_id}")
def delete_trip(trip_id: str):
    get_data_or_404(trip_id)
    db_delete(trip_id)
    return {"ok": True}

@app.post("/api/trips/{trip_id}/members")
def add_member(trip_id: str, body: AddMember):
    trip, payments = get_data_or_404(trip_id)
    if body.name in trip.members:
        raise HTTPException(status_code=400, detail=f"{body.name} is already on the trip")
    trip.add_member(body.name)
    db_save(trip_id, trip, payments)
    return trip_to_dict(trip_id, trip, payments)

@app.delete("/api/trips/{trip_id}/members/{member_name}")
def delete_member(trip_id: str, member_name: str):
    trip, payments = get_data_or_404(trip_id)
    if member_name not in trip.members:
        raise HTTPException(status_code=404, detail=f"{member_name} is not on the trip")
    involved = [e for e in trip.expenses if e.paid_by == member_name or member_name in e.split_among]
    if involved:
        raise HTTPException(status_code=400, detail=f"Cannot remove {member_name} — they have expenses. Delete those first.")
    trip.members.remove(member_name)
    db_save(trip_id, trip, payments)
    return trip_to_dict(trip_id, trip, payments)

@app.post("/api/trips/{trip_id}/expenses")
def add_expense(trip_id: str, body: AddExpense):
    trip, payments = get_data_or_404(trip_id)
    if body.paid_by not in trip.members:
        raise HTTPException(status_code=400, detail=f"{body.paid_by} is not on the trip")
    invalid = [p for p in body.split_among if p not in trip.members]
    if invalid:
        raise HTTPException(status_code=400, detail=f"{', '.join(invalid)} are not on the trip")
    if not body.split_among:
        raise HTTPException(status_code=400, detail="Must split among at least one person")
    trip.add_expense(body.description, body.amount, body.paid_by, body.split_among)
    db_save(trip_id, trip, payments)
    return trip_to_dict(trip_id, trip, payments)

@app.delete("/api/trips/{trip_id}/expenses/{expense_index}")
def delete_expense(trip_id: str, expense_index: int):
    trip, payments = get_data_or_404(trip_id)
    if expense_index < 0 or expense_index >= len(trip.expenses):
        raise HTTPException(status_code=404, detail="Expense not found")
    trip.expenses.pop(expense_index)
    db_save(trip_id, trip, payments)
    return trip_to_dict(trip_id, trip, payments)

@app.post("/api/trips/{trip_id}/payments")
def add_payment(trip_id: str, body: AddPayment):
    trip, payments = get_data_or_404(trip_id)
    if body.sent_by not in trip.members:
        raise HTTPException(status_code=400, detail=f"{body.sent_by} is not on the trip")
    if body.sent_to not in trip.members:
        raise HTTPException(status_code=400, detail=f"{body.sent_to} is not on the trip")
    if body.sent_by == body.sent_to:
        raise HTTPException(status_code=400, detail="Can't send money to yourself")
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    payments.append({"sent_by": body.sent_by, "sent_to": body.sent_to,
                     "amount": body.amount, "note": body.note})
    db_save(trip_id, trip, payments)
    return trip_to_dict(trip_id, trip, payments)

@app.delete("/api/trips/{trip_id}/payments/{payment_index}")
def delete_payment(trip_id: str, payment_index: int):
    trip, payments = get_data_or_404(trip_id)
    if payment_index < 0 or payment_index >= len(payments):
        raise HTTPException(status_code=404, detail="Payment not found")
    payments.pop(payment_index)
    db_save(trip_id, trip, payments)
    return trip_to_dict(trip_id, trip, payments)


# ── Serve frontend ─────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def index():
    return FileResponse("static/index.html")
