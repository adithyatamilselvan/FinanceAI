from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import get_db
from ..deps import CurrentUser
from ..schemas import Contribution, GoalIn, GoalUpdate
from ..services import analytics as an
from ..services import goals as gs
from ..util import clean_text, day_start, month_bounds, now_utc, to_oid

router = APIRouter(prefix="/goals", tags=["goals"])


def _today():
    return datetime.now(timezone.utc).date()


def _own(db, uid, goal_id: str) -> dict:
    g = db.financial_goals.find_one({"_id": to_oid(goal_id, "Goal"), "user_id": uid})
    if not g:
        raise HTTPException(status_code=404, detail="Goal not found")
    return g


@router.get("")
def list_goals(user: CurrentUser, db=Depends(get_db)):
    t = _today()
    return {"items": [gs.goal_view(g, t) for g in db.financial_goals.find({"user_id": user["_id"]}).sort("created_at", -1).limit(100)]}


@router.post("", status_code=201)
def create_goal(body: GoalIn, user: CurrentUser, db=Depends(get_db)):
    if db.financial_goals.count_documents({"user_id": user["_id"]}) >= 100:
        raise HTTPException(status_code=409, detail="Goal limit reached")
    doc = {"user_id": user["_id"], "name": clean_text(body.name, 80), "target_amount": body.target_amount,
           "current_amount": body.current_amount, "target_date": day_start(body.target_date) if body.target_date else None,
           "monthly_contribution": body.monthly_contribution or None, "created_at": now_utc(), "updated_at": now_utc()}
    doc["_id"] = db.financial_goals.insert_one(doc).inserted_id
    return gs.goal_view(doc, _today())


@router.get("/emergency-fund")
def emergency_fund(
    user: CurrentUser, db=Depends(get_db), months: int = Query(6, ge=1, le=36),
    monthly_contribution: int = Query(0, ge=0), basis: str = Query("essential", pattern="^(essential|total)$"),
    monthly_expense: int | None = Query(None, ge=1),
):
    uid = user["_id"]
    hist = an.completed_month_history(db, uid, _today(), 6)
    used = len(hist)
    if monthly_expense is not None:
        avg, used = monthly_expense, 0
    else:
        if not hist:
            raise HTTPException(status_code=422, detail="No completed months of transactions yet. Import transactions or enter your monthly expense manually.")
        meta = an.category_meta(db, uid)
        s, _ = month_bounds(hist[0]["period"])
        _, e = month_bounds(hist[-1]["period"])
        series = an.monthly_category_series(db, uid, s, e)
        periods = [h["period"] for h in hist]
        tot = 0
        for p in periods:
            for c, v in series.get(p, {}).items():
                if basis == "total" or meta.get(c, {}).get("essential"):
                    tot += v
        avg = tot // len(periods)
        if avg <= 0:
            raise HTTPException(status_code=422, detail="No essential spending found in the history; enter a monthly expense manually.")
    saved = int(user.get("current_savings") or 0)
    return gs.emergency_fund(avg, months, saved, monthly_contribution, basis if monthly_expense is None else "manual", used)


@router.get("/{goal_id}")
def get_goal(goal_id: str, user: CurrentUser, db=Depends(get_db)):
    return gs.goal_view(_own(db, user["_id"], goal_id), _today())


@router.patch("/{goal_id}")
def update_goal(goal_id: str, body: GoalUpdate, user: CurrentUser, db=Depends(get_db)):
    g = _own(db, user["_id"], goal_id)
    ch = body.model_dump(exclude_unset=True)
    if "name" in ch:
        ch["name"] = clean_text(ch["name"], 80)
    if "target_date" in ch:
        ch["target_date"] = day_start(ch["target_date"]) if ch["target_date"] else None
    if "monthly_contribution" in ch:
        ch["monthly_contribution"] = ch["monthly_contribution"] or None
    ch = {k: v for k, v in ch.items() if v is not None or k in ("target_date", "monthly_contribution")}
    ch["updated_at"] = now_utc()
    db.financial_goals.update_one({"_id": g["_id"]}, {"$set": ch})
    return gs.goal_view(db.financial_goals.find_one({"_id": g["_id"]}), _today())


@router.post("/{goal_id}/contributions")
def contribute(goal_id: str, body: Contribution, user: CurrentUser, db=Depends(get_db)):
    g = _own(db, user["_id"], goal_id)
    db.financial_goals.update_one({"_id": g["_id"]}, {"$inc": {"current_amount": body.amount}, "$set": {"updated_at": now_utc()}})
    return gs.goal_view(db.financial_goals.find_one({"_id": g["_id"]}), _today())


@router.delete("/{goal_id}", status_code=204)
def delete_goal(goal_id: str, user: CurrentUser, db=Depends(get_db)):
    res = db.financial_goals.delete_one({"_id": to_oid(goal_id, "Goal"), "user_id": user["_id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Goal not found")
