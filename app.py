from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uuid
import json
from pathlib import Path

from main import Trip, Expense

app = FastAPI(title="Splitting Fares")

DATA_FILE = Path("data.json")

# ── Persistence ────────────────────────────────────────────────────────────────

def load_trips() -> dict[str, Trip]:
    if not DATA_FILE.exists():
        return {}
    raw = json.loads(DATA_FILE.read_text())
    result = {}
    for tid, t in raw.items():
        trip = Trip(name=t["name"], members=t["members"])
        trip.expenses = [
            Expense(e["description"], e["amount"], e["paid_by"], e["split_among"])
            for e in t["expenses"]
        ]
        result[tid] = trip
    return result

def save_trips():
    data = {}
    for tid, trip in trips.items():
        data[tid] = {
            "name": trip.name,
            "members": trip.members,
            "expenses": [
                {"description": e.description, "amount": e.amount,
                 "paid_by": e.paid_by, "split_among": e.split_among}
                for e in trip.expenses
            ],
        }
    DATA_FILE.write_text(json.dumps(data, indent=2))

trips: dict[str, Trip] = load_trips()


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


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_trip(trip_id: str) -> Trip:
    trip = trips.get(trip_id)
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
            {
                "description": e.description,
                "amount": e.amount,
                "paid_by": e.paid_by,
                "split_among": e.split_among,
            }
            for e in trip.expenses
        ],
        "balances": [
            {
                "person": p,
                "amount": round(b, 2),
                "status": "owed" if b > 0.005 else ("owes" if b < -0.005 else "settled"),
            }
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
    trips[trip_id] = Trip(name=body.name)
    save_trips()
    return trip_to_dict(trip_id, trips[trip_id])

@app.get("/api/trips")
def list_trips():
    return [trip_to_dict(tid, t) for tid, t in trips.items()]

@app.get("/api/trips/{trip_id}")
def get_trip_detail(trip_id: str):
    trip = get_trip(trip_id)
    return trip_to_dict(trip_id, trip)

@app.post("/api/trips/{trip_id}/members")
def add_member(trip_id: str, body: AddMember):
    trip = get_trip(trip_id)
    if body.name in trip.members:
        raise HTTPException(status_code=400, detail=f"{body.name} is already on the trip")
    trip.add_member(body.name)
    save_trips()
    return trip_to_dict(trip_id, trip)

@app.delete("/api/trips/{trip_id}/members/{member_name}")
def delete_member(trip_id: str, member_name: str):
    trip = get_trip(trip_id)
    if member_name not in trip.members:
        raise HTTPException(status_code=404, detail=f"{member_name} is not on the trip")
    involved = [e for e in trip.expenses if e.paid_by == member_name or member_name in e.split_among]
    if involved:
        raise HTTPException(status_code=400, detail=f"Cannot remove {member_name} — they have expenses. Delete those first.")
    trip.members.remove(member_name)
    save_trips()
    return trip_to_dict(trip_id, trip)

@app.post("/api/trips/{trip_id}/expenses")
def add_expense(trip_id: str, body: AddExpense):
    trip = get_trip(trip_id)
    if body.paid_by not in trip.members:
        raise HTTPException(status_code=400, detail=f"{body.paid_by} is not on the trip")
    invalid = [p for p in body.split_among if p not in trip.members]
    if invalid:
        raise HTTPException(status_code=400, detail=f"{', '.join(invalid)} are not on the trip")
    if not body.split_among:
        raise HTTPException(status_code=400, detail="Must split among at least one person")
    trip.add_expense(body.description, body.amount, body.paid_by, body.split_among)
    save_trips()
    return trip_to_dict(trip_id, trip)

@app.delete("/api/trips/{trip_id}/expenses/{expense_index}")
def delete_expense(trip_id: str, expense_index: int):
    trip = get_trip(trip_id)
    if expense_index < 0 or expense_index >= len(trip.expenses):
        raise HTTPException(status_code=404, detail="Expense not found")
    trip.expenses.pop(expense_index)
    save_trips()
    return trip_to_dict(trip_id, trip)


# ── Serve frontend ─────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def index():
    return FileResponse("static/index.html")
