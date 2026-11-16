import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pymongo.errors import DuplicateKeyError

from ..db import get_db
from ..deps import CurrentUser
from ..schemas import BudgetIn, BudgetUpdate
from ..services import analytics as an
from ..util import PERIOD_RE, add_months, now_utc  # noqa: F401

router = APIRouter(prefix="/budgets", tags=["budgets"])


def _check_categories(db, uid, allocations) -> list[dict]:
    meta = an.category_meta(db, uid)
    out = []
    for a in allocations:
        c = next((k for k in meta if k.lower() == a.category.lower()), None)
        if c is None or meta[c]["type"] != "expense":
            raise HTTPException(status_code=422, detail=f"Unknown expense category '{a.category}'")
        out.append({"category": c, "amount": a.amount})
    return out


def _out(b: dict) -> dict:
    return {"id": str(b["_id"]), "period": b["period"], "allocations": b["allocations"],
            "total": sum(a["amount"] for a in b["allocations"]), "created_at": b["created_at"].isoformat(),
            "updated_at": b["updated_at"].isoformat()}


def _valid_period(p: str) -> str:
    if not re.match(PERIOD_RE, p):
        raise HTTPException(status_code=422, detail="Period must look like 2025-03")
    return p


@router.get("")
def list_budgets(user: CurrentUser, db=Depends(get_db)):
    return {"items": [_out(b) for b in db.budgets.find({"user_id": user["_id"]}).sort("period", -1).limit(120)]}


@router.post("", status_code=201)
def create_budget(body: BudgetIn, user: CurrentUser, db=Depends(get_db)):
    allocs = _check_categories(db, user["_id"], body.allocations)
    now = now_utc()
    doc = {"user_id": user["_id"], "period": body.period, "allocations": allocs, "created_at": now, "updated_at": now}
    try:
        doc["_id"] = db.budgets.insert_one(doc).inserted_id
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail=f"A budget for {body.period} already exists")
    return _out(doc)


@router.post("/copy", status_code=201)
def copy_budget(user: CurrentUser, db=Depends(get_db), from_period: str = Query(..., alias="from"), to_period: str = Query(..., alias="to")):
    src = db.budgets.find_one({"user_id": user["_id"], "period": _valid_period(from_period)})
    if not src:
        raise HTTPException(status_code=404, detail="Source budget not found")
    now = now_utc()
    doc = {"user_id": user["_id"], "period": _valid_period(to_period), "allocations": src["allocations"], "created_at": now, "updated_at": now}
    try:
        doc["_id"] = db.budgets.insert_one(doc).inserted_id
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail=f"A budget for {to_period} already exists")
    return _out(doc)


@router.get("/{period}")
def get_budget(period: str, user: CurrentUser, db=Depends(get_db)):
    b = db.budgets.find_one({"user_id": user["_id"], "period": _valid_period(period)})
    if not b:
        raise HTTPException(status_code=404, detail="Budget not found")
    return _out(b)


@router.get("/{period}/performance")
def budget_performance(period: str, user: CurrentUser, db=Depends(get_db)):
    return an.budget_performance(db, user["_id"], _valid_period(period))


@router.put("/{period}")
def update_budget(period: str, body: BudgetUpdate, user: CurrentUser, db=Depends(get_db)):
    allocs = _check_categories(db, user["_id"], body.allocations)
    if len({a["category"] for a in allocs}) != len(allocs):
        raise HTTPException(status_code=422, detail="Duplicate category in allocations")
    res = db.budgets.update_one({"user_id": user["_id"], "period": _valid_period(period)}, {"$set": {"allocations": allocs, "updated_at": now_utc()}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Budget not found")
    return _out(db.budgets.find_one({"user_id": user["_id"], "period": period}))


@router.delete("/{period}", status_code=204)
def delete_budget(period: str, user: CurrentUser, db=Depends(get_db)):
    res = db.budgets.delete_one({"user_id": user["_id"], "period": _valid_period(period)})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Budget not found")
