"""Recurring payments, analytics/anomalies, forecasts, optimisation and simulations."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import get_db
from ..deps import CurrentUser
from ..schemas import OptimizeIn, RecommendationAccept, RecurringUpdate, SimulationIn
from ..services import analytics as an
from ..services import anomaly
from ..services import forecast as fc
from ..services import goals as gs
from ..services import optimizer as opt
from ..services import recurring as rec
from ..services import simulation as sim
from ..util import PERIOD_RE, clean_text, month_bounds, now_utc, to_oid

router = APIRouter(tags=["insights"])


def _today():
    return datetime.now(timezone.utc).date()


# ---------------- analytics ----------------
@router.get("/analytics/dashboard")
def dashboard(user: CurrentUser, db=Depends(get_db), period: str | None = Query(None, pattern=PERIOD_RE)):
    d = an.dashboard(db, user["_id"], period, _today())
    d["currency"] = user.get("currency", "INR")
    d["health_score"] = an.health_score(db, user["_id"], _today())
    d["budget"] = an.budget_performance(db, user["_id"], d["period"])
    return d


@router.get("/analytics/monthly/{period}")
def monthly(period: str, user: CurrentUser, db=Depends(get_db)):
    if len(period) != 7:
        raise HTTPException(status_code=422, detail="Period must look like 2025-03")
    return an.dashboard(db, user["_id"], period, _today())


@router.get("/analytics/yearly/{year}")
def yearly(year: int, user: CurrentUser, db=Depends(get_db)):
    if not 1990 <= year <= 2100:
        raise HTTPException(status_code=422, detail="Year out of range")
    return an.yearly(db, user["_id"], year)


@router.get("/analytics/category-trends")
def category_trends(user: CurrentUser, db=Depends(get_db), months: int = Query(6, ge=2, le=24)):
    return an.category_trends(db, user["_id"], months, _today())


@router.get("/analytics/anomalies")
def anomalies(user: CurrentUser, db=Depends(get_db)):
    return anomaly.detect(db, user["_id"], user.get("currency", "INR"))


@router.get("/analytics/health-score")
def health(user: CurrentUser, db=Depends(get_db)):
    return an.health_score(db, user["_id"], _today())


# ---------------- recurring ----------------
@router.post("/recurring/detect")
def detect_recurring(user: CurrentUser, db=Depends(get_db)):
    return rec.detect_and_store(db, user["_id"], _today())


@router.get("/recurring")
def list_recurring(user: CurrentUser, db=Depends(get_db), include_dismissed: bool = False):
    f: dict = {"user_id": user["_id"]}
    if not include_dismissed:
        f["status"] = {"$ne": "dismissed"}
    items = [rec.recurring_out(d) for d in db.recurring_payments.find(f).sort("estimated_monthly", -1).limit(200)]
    # Recurring COST = bills/subscriptions plus anything the user explicitly confirmed. Habitual spending (groceries, fuel...)
    # is shown for review but not counted unless confirmed.
    counted = [i for i in items if i["status"] != "dismissed" and (i["kind"] == "bill_or_subscription" or i["status"] == "confirmed")]
    return {"items": items, "estimated_monthly_total": sum(i["estimated_monthly"] for i in counted),
            "estimated_annual_total": sum(i["estimated_monthly"] for i in counted) * 12, "counted_items": len(counted),
            "note": "Estimates are derived from your past transactions. Totals count bills/subscriptions and payments you confirmed; habitual spending (groceries, fuel, cabs) is listed but excluded until you confirm it. Nothing is cancelled or changed automatically."}


@router.patch("/recurring/{rid}")
def update_recurring(rid: str, body: RecurringUpdate, user: CurrentUser, db=Depends(get_db)):
    d = db.recurring_payments.find_one({"_id": to_oid(rid, "Recurring payment"), "user_id": user["_id"]})
    if not d:
        raise HTTPException(status_code=404, detail="Recurring payment not found")
    ch = body.model_dump(exclude_unset=True)
    if "name" in ch:
        ch["name"] = clean_text(ch["name"], 100)
    if {"name", "amount", "frequency"} & set(ch):
        ch["user_edited"] = True
        amount, freq = ch.get("amount", d["amount"]), ch.get("frequency", d["frequency"])
        ch["estimated_monthly"] = rec.estimated_monthly(amount, freq)
    ch["updated_at"] = now_utc()
    db.recurring_payments.update_one({"_id": d["_id"]}, {"$set": ch})
    return rec.recurring_out(db.recurring_payments.find_one({"_id": d["_id"]}))


# ---------------- forecast ----------------
@router.get("/forecasts")
def get_forecast(user: CurrentUser, db=Depends(get_db), horizon: int = Query(3, ge=1, le=12)):
    uid = user["_id"]
    goals = [gs.goal_view(g, _today()) for g in db.financial_goals.find({"user_id": uid}).limit(100)]
    recurring_items = [rec.recurring_out(d) for d in db.recurring_payments.find({"user_id": uid, "status": {"$ne": "dismissed"}}).limit(200)]
    out = fc.forecast(db, uid, horizon, _today(), goals, recurring_items)
    out["currency"] = user.get("currency", "INR")
    if out.get("sufficient"):
        db.forecasts.replace_one({"user_id": uid}, {"user_id": uid, "created_at": now_utc(), "horizon": horizon, "result": out}, upsert=True)
    return out


# ---------------- optimisation ----------------
@router.post("/optimization/run", status_code=201)
def run_optimization(body: OptimizeIn, user: CurrentUser, db=Depends(get_db)):
    uid = user["_id"]
    result = opt.optimize(db, uid, body.months, body.min_spend, _today())
    if not result.get("sufficient"):
        raise HTTPException(status_code=422, detail=result["reason"])
    doc = {"user_id": uid, "created_at": now_utc(), "status": "pending", "params": body.model_dump(), **result}
    doc["_id"] = db.recommendations.insert_one(doc).inserted_id
    return {"id": str(doc["_id"]), "status": "pending", **result}


def _rec_out(d: dict) -> dict:
    out = {k: v for k, v in d.items() if k not in ("_id", "user_id", "created_at")}
    out.update(id=str(d["_id"]), created_at=d["created_at"].isoformat())
    return out


@router.get("/optimization/recommendations")
def list_recommendations(user: CurrentUser, db=Depends(get_db)):
    cur = db.recommendations.find({"user_id": user["_id"]}, projection={"scenarios": 0, "fixed_categories": 0}).sort("created_at", -1).limit(30)
    return {"items": [{"id": str(d["_id"]), "status": d["status"], "created_at": d["created_at"].isoformat(),
                       "accepted_scenario": d.get("accepted_scenario")} for d in cur]}


@router.get("/optimization/recommendations/{rid}")
def get_recommendation(rid: str, user: CurrentUser, db=Depends(get_db)):
    d = db.recommendations.find_one({"_id": to_oid(rid, "Recommendation"), "user_id": user["_id"]})
    if not d:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return _rec_out(d)


@router.post("/optimization/recommendations/{rid}/accept")
def accept_recommendation(rid: str, body: RecommendationAccept, user: CurrentUser, db=Depends(get_db)):
    uid = user["_id"]
    d = db.recommendations.find_one({"_id": to_oid(rid, "Recommendation"), "user_id": uid})
    if not d:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if d["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"Recommendation already {d['status']}")
    allocs = [a.model_dump() for a in body.allocations] if body.allocations else opt.budget_allocations(d, body.scenario)
    meta = an.category_meta(db, uid)
    for a in allocs:
        if meta.get(a["category"], {}).get("type") != "expense":
            raise HTTPException(status_code=422, detail=f"Unknown expense category '{a['category']}'")
    applied = None
    if body.apply_to_budget_period:  # explicit user action only
        db.budgets.update_one({"user_id": uid, "period": body.apply_to_budget_period},
                              {"$set": {"allocations": allocs, "updated_at": now_utc()}, "$setOnInsert": {"created_at": now_utc()}}, upsert=True)
        applied = body.apply_to_budget_period
    db.recommendations.update_one({"_id": d["_id"]}, {"$set": {"status": "accepted", "accepted_scenario": body.scenario, "accepted_allocations": allocs,
                                                                "applied_to_period": applied, "decided_at": now_utc()}})
    return {"status": "accepted", "scenario": body.scenario, "allocations": allocs, "applied_to_budget_period": applied}


@router.post("/optimization/recommendations/{rid}/reject")
def reject_recommendation(rid: str, user: CurrentUser, db=Depends(get_db)):
    res = db.recommendations.update_one({"_id": to_oid(rid, "Recommendation"), "user_id": user["_id"], "status": "pending"},
                                        {"$set": {"status": "rejected", "decided_at": now_utc()}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Pending recommendation not found")
    return {"status": "rejected"}


# ---------------- simulation ----------------
def _baseline(db, user) -> tuple[int, int, dict[str, int]]:
    uid = user["_id"]
    hist = an.completed_month_history(db, uid, _today(), 6)
    if not hist:
        return int(user.get("monthly_income") or 0), 0, {}
    n = len(hist)
    s, _ = month_bounds(hist[0]["period"])
    _, e = month_bounds(hist[-1]["period"])
    series = an.monthly_category_series(db, uid, s, e)
    cat: dict[str, int] = {}
    for m in series.values():
        for c, v in m.items():
            cat[c] = cat.get(c, 0) + v
    cat = {c: v // n for c, v in cat.items()}
    return sum(h["income"] for h in hist) // n, sum(h["net_expense"] for h in hist) // n, cat


@router.post("/simulations/run")
def run_simulation(body: SimulationIn, user: CurrentUser, db=Depends(get_db)):
    inc, exp, cats = _baseline(db, user)
    if body.base_monthly_income is not None:
        inc = body.base_monthly_income
    if body.base_monthly_expense is not None:
        exp = body.base_monthly_expense
    # Never assume zero spending: without real history the user must state their spending (otherwise "savings" = whole income).
    if exp == 0 and not cats and body.base_monthly_expense is None:
        raise HTTPException(status_code=422, detail="No income/expense history found. Import transactions or enter your base monthly spending (and income).")
    if inc == 0 and exp == 0:
        raise HTTPException(status_code=422, detail="No income/expense history found. Import transactions or enter base monthly income and expense.")
    start = int(user.get("current_savings") or 0)  # baseline always starts from the real profile balance
    scenario_start = body.starting_savings if body.starting_savings is not None else start
    p = body.model_dump()
    p["category_cuts"] = [c.model_dump() if hasattr(c, "model_dump") else c for c in body.category_cuts]
    try:
        out = sim.simulate(p, inc, exp, cats, start, scenario_start)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    out["currency"] = user.get("currency", "INR")
    if body.save:
        d = {"user_id": user["_id"], "name": clean_text(body.name or "Scenario", 80), "created_at": now_utc(), "params": body.model_dump(exclude={"save"}),
             "summary": {"baseline": out["baseline"], "scenario": out["scenario"], "difference": out["difference"]}}
        out["id"] = str(db.simulations.insert_one(d).inserted_id)
    return out


@router.get("/simulations")
def list_simulations(user: CurrentUser, db=Depends(get_db)):
    return {"items": [{"id": str(d["_id"]), "name": d["name"], "created_at": d["created_at"].isoformat(), "params": d["params"], "summary": d["summary"]}
                      for d in db.simulations.find({"user_id": user["_id"]}).sort("created_at", -1).limit(50)]}


@router.delete("/simulations/{sid}", status_code=204)
def delete_simulation(sid: str, user: CurrentUser, db=Depends(get_db)):
    if db.simulations.delete_one({"_id": to_oid(sid, "Simulation"), "user_id": user["_id"]}).deleted_count == 0:
        raise HTTPException(status_code=404, detail="Simulation not found")


@router.get("/simulations/loan")
def loan_calc(user: CurrentUser, principal: int = Query(..., gt=0, le=10**13), annual_rate_pct: float = Query(..., ge=0, le=100),
              tenure_months: int = Query(..., ge=1, le=600), extra_monthly_payment: int = Query(0, ge=0, le=10**13)):
    try:
        return sim.loan_analysis(principal, annual_rate_pct, tenure_months, extra_monthly_payment)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

