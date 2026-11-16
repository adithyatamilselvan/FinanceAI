from datetime import date

import pytest
from openpyxl import load_workbook

from app.services import forecast as fc
from app.services import goals as gs
from app.services import optimizer as opt
from app.services import recurring as rec
from app.util import add_months
from tests.conftest import API, import_statement, make_statement, recent_start


def seed(client, auth, n=4):
    start = recent_start(n)
    import_statement(client, auth, make_statement(n, start))
    return [add_months(start, i) for i in range(n)]


def add_tx(client, auth, d, desc, amount, typ="expense", category=None):
    body = {"date": d, "description": desc, "amount": amount, "type": typ}
    if category:
        body["category"] = category
    r = client.post(f"{API}/transactions", headers=auth, json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------- analytics ----------------
def test_dashboard_known_values_and_reconciliation(client, auth):
    periods = seed(client, auth, 4)
    last = periods[-1]
    d = client.get(f"{API}/analytics/dashboard", headers=auth, params={"period": last}).json()
    t = d["totals"]
    assert t["income"] == 10000000 and t["net_expense"] == 3859900 and t["net_cash_flow"] == 6140100
    assert t["savings_pct"] == pytest.approx(61.4, abs=0.01)
    cats = {c["category"]: c["amount"] for c in d["categories"]}
    assert cats["Groceries"] == 430000 and cats["Housing"] == 3000000 and cats["Subscriptions"] == 64900
    assert sum(cats.values()) == t["net_expense"]  # category breakdown reconciles with the total
    assert d["fixed_variable"]["fixed"] == 3064900 and d["fixed_variable"]["variable"] == 795000
    assert d["vs_previous_month"]["expense_change"] == 15000 and d["vs_previous_month"]["income_change"] == 0
    assert len(d["trend"]) == 4 and d["trend"][0]["net_expense"] == 3814900
    # reconcile against raw transactions
    txs = client.get(f"{API}/transactions", headers=auth, params={"page_size": 100, "date_from": f"{last}-01", "date_to": f"{last}-31"}).json()["items"]
    assert sum(x["amount"] for x in txs if x["type"] == "expense") == t["net_expense"]
    assert sum(x["amount"] for x in txs if x["type"] == "income") == t["income"]
    assert d["has_transactions"] is True and d["currency"] == "INR"


def test_empty_dashboard_is_honest(client, auth):
    d = client.get(f"{API}/analytics/dashboard", headers=auth).json()
    assert d["has_transactions"] is False and d["totals"]["income"] == 0 and d["categories"] == [] and d["health_score"]["score"] is None


def test_month_boundaries_and_leap_year(client, auth):
    add_tx(client, auth, "2024-02-29", "Leap day shop", 10000)
    add_tx(client, auth, "2024-03-01", "March first shop", 20000)
    add_tx(client, auth, "2024-01-31", "Jan last day", 30000)
    add_tx(client, auth, "2023-12-31", "NYE", 40000)
    g = lambda p: client.get(f"{API}/analytics/monthly/{p}", headers=auth).json()["totals"]["net_expense"]  # noqa: E731
    assert (g("2024-02"), g("2024-03"), g("2024-01"), g("2023-12")) == (10000, 20000, 30000, 40000)
    y = client.get(f"{API}/analytics/yearly/2024", headers=auth).json()
    assert y["totals"]["net_expense"] == 60000 and y["vs_previous_year"]["previous"]["net_expense"] == 40000


def test_refunds_and_transfers_in_totals(client, auth):
    add_tx(client, auth, "2025-05-01", "Shoes", 100000, category="Shopping")
    add_tx(client, auth, "2025-05-03", "Shoes refund", 30000, "refund", category="Shopping")
    add_tx(client, auth, "2025-05-04", "Move to savings", 500000, "transfer")
    add_tx(client, auth, "2025-05-05", "Pay", 900000, "income", category="Salary")
    t = client.get(f"{API}/analytics/monthly/2025-05", headers=auth).json()
    assert t["totals"]["net_expense"] == 70000 and t["totals"]["refunds"] == 30000 and t["totals"]["income"] == 900000
    assert t["categories"][0] == {"category": "Shopping", "amount": 70000, "pct": 100.0, "fixed": False}
    assert t["totals"]["net_cash_flow"] == 830000  # transfer ignored


def test_category_trends_and_health_score(client, auth):
    seed(client, auth, 6)
    client.patch(f"{API}/profile", headers=auth, json={"current_savings": 20000000})
    tr = client.get(f"{API}/analytics/category-trends", headers=auth, params={"months": 6}).json()
    g = next(c for c in tr["categories"] if c["category"] == "Groceries")
    assert g["values"] == [400000 + 10000 * i for i in range(6)] and g["direction"] == "up" and g["trend_per_month"] == 10000
    h = client.get(f"{API}/analytics/health-score", headers=auth).json()
    assert 0 < h["score"] <= 100 and {c["name"] for c in h["components"]} >= {"Savings rate", "Spending stability", "Emergency fund"}
    assert all(0 <= c["score"] <= c["max"] for c in h["components"]) and "not a credit score" in h["note"]


# ---------------- budgets ----------------
def test_budget_lifecycle_and_utilization(client, auth):
    last = seed(client, auth, 4)[-1]
    body = {"period": last, "allocations": [{"category": "Groceries", "amount": 400000}, {"category": "Food", "amount": 150000},
                                            {"category": "Housing", "amount": 3000000}, {"category": "Entertainment", "amount": 200000}]}
    r = client.post(f"{API}/budgets", headers=auth, json=body)
    assert r.status_code == 201 and r.json()["total"] == 3750000
    assert client.post(f"{API}/budgets", headers=auth, json=body).status_code == 409
    p = client.get(f"{API}/budgets/{last}/performance", headers=auth).json()
    rows = {x["category"]: x for x in p["rows"]}
    assert rows["Groceries"]["actual"] == 430000 and rows["Groceries"]["over_budget"] is True and rows["Groceries"]["utilization_pct"] == 107.5
    assert rows["Food"]["over_budget"] is True and rows["Food"]["remaining"] == -15000
    assert rows["Housing"]["over_budget"] is False and rows["Housing"]["utilization_pct"] == 100.0
    assert rows["Entertainment"]["utilization_pct"] == 60.0 and rows["Transport"]["unbudgeted"] is True
    assert set(p["overspent_categories"]) == {"Groceries", "Food"}
    assert p["total_budget"] == 3750000 and p["total_actual"] == 3859900 and p["remaining"] == -109900
    up = client.put(f"{API}/budgets/{last}", headers=auth, json={"allocations": [{"category": "Groceries", "amount": 500000}]}).json()
    assert up["total"] == 500000
    assert client.get(f"{API}/budgets", headers=auth).json()["items"][0]["period"] == last
    cp = client.post(f"{API}/budgets/copy", headers=auth, params={"from": last, "to": "2099-01"})
    assert cp.status_code == 201
    assert client.delete(f"{API}/budgets/{last}", headers=auth).status_code == 204
    assert client.get(f"{API}/budgets/{last}", headers=auth).status_code == 404


def test_budget_validation(client, auth):
    ok = [{"category": "Food", "amount": 100}]
    assert client.post(f"{API}/budgets", headers=auth, json={"period": "2025-13", "allocations": ok}).status_code == 422
    assert client.post(f"{API}/budgets", headers=auth, json={"period": "2025-01", "allocations": [{"category": "Food", "amount": -1}]}).status_code == 422
    assert client.post(f"{API}/budgets", headers=auth, json={"period": "2025-01", "allocations": [{"category": "Salary", "amount": 1}]}).status_code == 422
    assert client.post(f"{API}/budgets", headers=auth, json={"period": "2025-01", "allocations": ok + ok}).status_code == 422
    assert client.post(f"{API}/budgets", headers=auth, json={"period": "2025-01", "allocations": []}).status_code == 422


# ---------------- goals ----------------
def test_goal_math_known_values():
    g = {"_id": "1", "name": "Car", "target_amount": 10000000, "current_amount": 2500000, "target_date": date(2025, 7, 15), "monthly_contribution": 1000000,
         "created_at": __import__("datetime").datetime(2025, 1, 1)}
    v = gs.goal_view(g, date(2025, 1, 15))
    assert v["remaining"] == 7500000 and v["progress_pct"] == 25.0 and v["months_remaining"] == 6
    assert v["required_monthly_contribution"] == 1250000  # ceil(7,500,000 / 6)
    assert v["estimated_completion"] == "2025-09-15" and v["on_track"] is False and v["achieved"] is False
    g2 = {**g, "current_amount": 10000000}
    assert gs.goal_view(g2, date(2025, 1, 15))["achieved"] is True
    assert gs.add_months_to_date(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert gs.months_until(date(2025, 1, 15), date(2025, 1, 15)) == 0 and gs.months_until(date(2025, 1, 15), date(2025, 1, 16)) == 1


def test_goals_api(client, auth):
    far = f"{date.today().year + 2}-06-30"
    r = client.post(f"{API}/goals", headers=auth, json={"name": "Trip", "target_amount": 5000000, "current_amount": 1000000, "target_date": far, "monthly_contribution": 500000})
    assert r.status_code == 201
    g = r.json()
    assert g["progress_pct"] == 20.0 and g["remaining"] == 4000000 and g["estimated_completion"] is not None and g["on_track"] is True
    c = client.post(f"{API}/goals/{g['id']}/contributions", headers=auth, json={"amount": 1500000}).json()
    assert c["current_amount"] == 2500000 and c["progress_pct"] == 50.0
    assert client.post(f"{API}/goals/{g['id']}/contributions", headers=auth, json={"amount": 0}).status_code == 422
    u = client.patch(f"{API}/goals/{g['id']}", headers=auth, json={"target_amount": 2500000}).json()
    assert u["achieved"] is True
    assert len(client.get(f"{API}/goals", headers=auth).json()["items"]) == 1
    assert client.post(f"{API}/goals", headers=auth, json={"name": "x", "target_amount": 0}).status_code == 422
    assert client.delete(f"{API}/goals/{g['id']}", headers=auth).status_code == 204


def test_emergency_fund(client, auth):
    assert client.get(f"{API}/goals/emergency-fund", headers=auth).status_code == 422  # no history
    seed(client, auth, 4)
    client.patch(f"{API}/profile", headers=auth, json={"current_savings": 5000000})
    e = client.get(f"{API}/goals/emergency-fund", headers=auth, params={"months": 6, "monthly_contribution": 1000000}).json()
    assert e["average_monthly_expense"] == 3415000  # Housing 30,000 + avg Groceries 4,150 (essential only)
    assert e["target"] == 20490000 and e["gap"] == 15490000 and e["months_to_goal"] == 16 and e["history_months_used"] == 4
    assert e["assumptions"]
    t = client.get(f"{API}/goals/emergency-fund", headers=auth, params={"basis": "total", "months": 3}).json()
    assert t["average_monthly_expense"] == 3837400  # mean of 38,149 / 38,299 / 38,449 / 38,599
    m = client.get(f"{API}/goals/emergency-fund", headers=auth, params={"monthly_expense": 100000, "months": 3}).json()
    assert m["target"] == 300000 and m["basis"] == "manual"


# ---------------- recurring ----------------
def test_recurring_detection_and_user_control(client, auth):
    seed(client, auth, 6)
    r = client.post(f"{API}/recurring/detect", headers=auth)
    assert r.status_code == 200 and r.json()["detected"] >= 4
    items = client.get(f"{API}/recurring", headers=auth).json()
    nf = next(i for i in items["items"] if "netflix" in i["name"].lower())
    assert nf["frequency"] == "monthly" and nf["amount"] == 64900 and nf["estimated_monthly"] == 64900 and nf["confidence"] >= 0.9
    ev = nf["evidence"]
    assert ev["occurrences"] == 6 and ev["interval_consistency"] == 1.0 and ev["amount_consistency"] == 1.0 and ev["explanation"]
    names = " ".join(i["name"].lower() for i in items["items"])
    assert "salary" not in names  # income is never a recurring payment
    # total = bills/subscriptions only (rent 30,000.00 + Netflix 649.00); habitual spending (groceries, cabs...) is listed but not counted
    assert items["estimated_monthly_total"] == 3064900 and items["estimated_annual_total"] == 3064900 * 12
    assert {i["kind"] for i in items["items"] if i["name"].lower().startswith(("bigbasket", "uber"))} == {"habitual_spending"}
    p = client.patch(f"{API}/recurring/{nf['id']}", headers=auth, json={"status": "confirmed"}).json()
    assert p["status"] == "confirmed"
    e = client.patch(f"{API}/recurring/{nf['id']}", headers=auth, json={"amount": 79900, "frequency": "annual", "name": "Netflix yearly"}).json()
    assert e["estimated_monthly"] == 6658 and e["user_edited"] is True  # 79900 / 12 rounded
    other = next(i for i in items["items"] if "uber" in i["name"].lower())
    client.patch(f"{API}/recurring/{other['id']}", headers=auth, json={"status": "dismissed"})
    client.post(f"{API}/recurring/detect", headers=auth)  # re-detect must keep user decisions
    after = client.get(f"{API}/recurring", headers=auth).json()["items"]
    assert not any("uber" in i["name"].lower() for i in after)
    kept = next(i for i in after if i["id"] == nf["id"])
    assert kept["status"] == "confirmed" and kept["amount"] == 79900 and kept["name"] == "Netflix yearly"
    assert any("uber" in i["name"].lower() for i in client.get(f"{API}/recurring", headers=auth, params={"include_dismissed": True}).json()["items"])


def _tx(d, desc, amount, key):
    from datetime import datetime, timezone

    y, m, dd = map(int, d.split("-"))
    return {"date": datetime(y, m, dd, tzinfo=timezone.utc), "description": desc, "amount": amount, "merchant_key": key, "category": "Other"}


def test_recurring_unit_frequencies_and_negatives():
    ref = date(2025, 12, 31)
    annual = [_tx("2023-03-10", "Annual Premium", 1200000, "annual premium"), _tx("2024-03-11", "Annual Premium", 1200000, "annual premium")]
    weekly = [_tx(f"2025-11-{d:02d}", "Weekly Maid", 50000, "weekly maid") for d in (3, 10, 17, 24)] + [_tx("2025-12-01", "Weekly Maid", 50000, "weekly maid")]
    irregular = [_tx(d, "Random Shop", a, "random shop") for d, a in [("2025-01-03", 100), ("2025-01-09", 9000), ("2025-03-30", 500), ("2025-06-01", 700)]]
    two_only = [_tx("2025-10-01", "Gym", 1000, "gym"), _tx("2025-11-01", "Gym", 1000, "gym")]
    out = {c["merchant_key"]: c for c in rec.detect_candidates(annual + weekly + irregular + two_only, ref)}
    assert out["annual premium"]["frequency"] == "annual" and out["annual premium"]["estimated_monthly"] == 100000
    assert out["weekly maid"]["frequency"] == "weekly" and out["weekly maid"]["estimated_monthly"] == 216667  # 50000*52/12
    assert "random shop" not in out and "gym" not in out  # irregular intervals and too little evidence are not guessed
    assert rec.estimated_monthly(30000, "quarterly") == 10000


# ---------------- anomalies ----------------
def test_anomaly_detection(client, auth):
    s = recent_start(4)
    for i in range(8):
        add_tx(client, auth, f"{add_months(s, i // 3)}-{10 + i:02d}", f"Corner Cafe {i}", 50000 + 1000 * i, category="Food")
    big = add_tx(client, auth, f"{add_months(s, 3)}-15", "Fancy Banquet", 900000, category="Food")
    for k in range(3):  # shopping: ~1000/month then a spike in the latest month
        add_tx(client, auth, f"{add_months(s, k)}-05", "Mall visit", 100000, category="Shopping")
        add_tx(client, auth, f"{add_months(s, k)}-06", "Rent", 3000000, category="Housing")
        add_tx(client, auth, f"{add_months(s, k)}-07", "Groceries", 500000, category="Groceries")
    add_tx(client, auth, f"{add_months(s, 3)}-05", "Mall visit big", 600000, category="Shopping")
    add_tx(client, auth, f"{add_months(s, 3)}-06", "Rent", 3000000, category="Housing")
    add_tx(client, auth, f"{add_months(s, 3)}-07", "Groceries", 500000, category="Groceries")
    add_tx(client, auth, f"{add_months(s, 3)}-20", "Gadget Hub", 250000, category="Shopping")
    add_tx(client, auth, f"{add_months(s, 3)}-21", "Gadget Hub", 250000, category="Shopping")
    a = client.get(f"{API}/analytics/anomalies", headers=auth).json()
    assert any(x["transaction_id"] == big["id"] and x["ratio"] > 10 for x in a["large_transactions"])
    assert not any(x["description"].startswith("Corner Cafe") for x in a["large_transactions"])
    assert any(x["category"] == "Shopping" for x in a["category_spikes"]) and not any(x["category"] == "Housing" for x in a["category_spikes"])
    assert any(x["description"] == "Gadget Hub" and x["amount"] == 250000 for x in a["possible_duplicates"])
    assert "not necessarily wrong or fraudulent" in a["disclaimer"] and "fraud" not in " ".join(x["reason"] for x in a["large_transactions"]).lower()


def test_anomaly_empty(client, auth):
    a = client.get(f"{API}/analytics/anomalies", headers=auth).json()
    assert a["large_transactions"] == [] and a["category_spikes"] == [] and a["possible_duplicates"] == []


# ---------------- forecast ----------------
def test_forecast_math_known_values():
    flat = fc._project([100, 100, 100, 100], 3)
    assert [p["mid"] for p in flat["points"]] == [100, 100, 100] and flat["points"][0]["low"] == 100 and flat["slope_per_month"] == 0
    up = fc._project([100, 110, 120, 130, 140], 2)
    assert up["baseline"] == 133  # (120*1 + 130*2 + 140*3) / 6
    assert up["slope_per_month"] == 10 and up["points"][0]["mid"] == 138 and up["points"][1]["mid"] == 143  # 133.33 + 10*h*0.5
    assert up["points"][0]["low"] < up["points"][0]["mid"] < up["points"][0]["high"]
    capped = fc._project([100, 100, 100, 1000], 12)  # trend adjustment is capped at 20% of baseline
    assert all(p["mid"] <= capped["baseline"] * 1.2 + 1 for p in capped["points"])


def test_forecast_refuses_with_insufficient_history(client, auth):
    seed(client, auth, 2)
    f = client.get(f"{API}/forecasts", headers=auth).json()
    assert f["sufficient"] is False and f["history_months"] == 2 and "at least 3" in f["reason"]
    assert "income" not in f


def test_forecast_api(client, auth):
    periods = seed(client, auth, 6)
    client.patch(f"{API}/profile", headers=auth, json={"current_savings": 1000000})
    client.post(f"{API}/recurring/detect", headers=auth)
    client.post(f"{API}/goals", headers=auth, json={"name": "G", "target_amount": 100000000, "current_amount": 0})
    f = client.get(f"{API}/forecasts", headers=auth, params={"horizon": 3}).json()
    assert f["sufficient"] and f["history_months"] == 6 and f["history_from"] == periods[0] and f["history_to"] == periods[-1] and f["confidence"] == "medium"
    assert [m["period"] for m in f["months"]] == [add_months(periods[-1], i) for i in (1, 2, 3)]
    m0 = f["months"][0]
    assert m0["net_savings"] == m0["income"]["mid"] - m0["expense"]["mid"] and m0["cumulative_savings"] == 1000000 + m0["net_savings"]
    assert m0["recurring_expected"] > 0 and f["goal_estimates"][0]["status"] == "projected" and f["goal_estimates"][0]["months_needed"] > 10
    assert 3800000 < m0["expense"]["mid"] < 4000000 and any("Based on 6 completed" in x for x in f["method"])


# ---------------- optimiser ----------------
def test_solver_beats_or_matches_fallback_and_respects_bounds():
    c, w, cap = [400000, 150000, 80000, 120000], [8.0, 1.0, 3.0, 1.0], [20000, 45000, 8000, 36000]
    target = 70000
    red, solver = opt._solve(c, w, cap, target)
    assert sum(red) >= target and all(0 <= r <= k for r, k in zip(red, cap, strict=True)) and solver in {"scipy-slsqp", "water-filling-fallback"}
    wf = opt._waterfill(c, w, cap, target)
    cost = lambda r: sum(w[i] * r[i] ** 2 / c[i] for i in range(4))  # noqa: E731
    assert sum(wf) >= target and cost(red) <= cost(wf) * 1.01
    assert opt._solve(c, w, cap, 10**9)[0] == cap  # infeasible target -> cut everything to the cap, never beyond


def test_optimization_flow(client, auth):
    seed(client, auth, 6)
    r = client.post(f"{API}/optimization/run", headers=auth, json={"months": 6})
    assert r.status_code == 201
    rec_ = r.json()
    assert rec_["history_months"] == 6 and "not guaranteed" in rec_["disclaimer"]
    fixed = {f["category"]: f["monthly"] for f in rec_["fixed_categories"]}
    assert set(fixed) == {"Housing", "Subscriptions"} and fixed["Housing"] == 3000000
    saved = []
    for name in ("conservative", "balanced", "aggressive"):
        sc = rec_["scenarios"][name]
        assert sc["valid"] and sc["target_reached"] and sc["achieved_savings"] >= sc["target_savings"] > 0
        assert sc["annual_savings"] == sc["achieved_savings"] * 12
        assert sum(c["current"] - c["suggested"] for c in sc["categories"]) == sc["achieved_savings"]
        for c in sc["categories"]:
            assert c["minimum_allowed"] <= c["suggested"] <= c["current"]
            assert c["class"] != "essential" or name != "conservative" or c["suggested"] == c["current"]
        saved.append(sc["achieved_savings"])
    assert saved[0] < saved[1] < saved[2]
    cons = {c["category"]: c for c in rec_["scenarios"]["conservative"]["categories"]}
    assert cons["Groceries"]["suggested"] == cons["Groceries"]["current"]  # essential spending is protected
    # user minimum respected
    cur_ent = cons["Entertainment"]["current"]
    r2 = client.post(f"{API}/optimization/run", headers=auth, json={"months": 6, "min_spend": {"Entertainment": cur_ent}}).json()
    ent = next(c for c in r2["scenarios"]["aggressive"]["categories"] if c["category"] == "Entertainment")
    assert ent["suggested"] == cur_ent
    # nothing changes automatically
    assert client.get(f"{API}/budgets", headers=auth).json()["items"] == []
    period = add_months(recent_start(1), 0)
    a = client.post(f"{API}/optimization/recommendations/{rec_['id']}/accept", headers=auth, json={"scenario": "balanced"}).json()
    assert a["status"] == "accepted" and a["applied_to_budget_period"] is None
    assert client.get(f"{API}/budgets", headers=auth).json()["items"] == []  # accepting alone does not touch budgets
    assert client.post(f"{API}/optimization/recommendations/{rec_['id']}/accept", headers=auth, json={"scenario": "balanced"}).status_code == 409
    r3 = client.post(f"{API}/optimization/run", headers=auth, json={"months": 6}).json()
    ap = client.post(f"{API}/optimization/recommendations/{r3['id']}/accept", headers=auth,
                     json={"scenario": "aggressive", "apply_to_budget_period": period}).json()
    assert ap["applied_to_budget_period"] == period
    b = client.get(f"{API}/budgets/{period}", headers=auth).json()
    got = {x["category"]: x["amount"] for x in b["allocations"]}
    assert got["Housing"] == 3000000 and got["Food"] == next(c["suggested"] for c in r3["scenarios"]["aggressive"]["categories"] if c["category"] == "Food")
    r4 = client.post(f"{API}/optimization/recommendations/{r3['id']}/reject", headers=auth)
    assert r4.status_code == 404  # already decided
    r5 = client.post(f"{API}/optimization/run", headers=auth, json={"months": 6}).json()
    assert client.post(f"{API}/optimization/recommendations/{r5['id']}/reject", headers=auth).json()["status"] == "rejected"
    assert len(client.get(f"{API}/optimization/recommendations", headers=auth).json()["items"]) == 4


def test_optimization_needs_history_and_isolated(client, auth):
    assert client.post(f"{API}/optimization/run", headers=auth, json={}).status_code == 422
    seed(client, auth, 3)
    rid = client.post(f"{API}/optimization/run", headers=auth, json={}).json()["id"]
    from tests.conftest import register

    other = register(client, "other@example.com")
    assert client.get(f"{API}/optimization/recommendations/{rid}", headers=other).status_code == 404
    assert client.post(f"{API}/optimization/recommendations/{rid}/accept", headers=other, json={"scenario": "balanced"}).status_code == 404


# ---------------- simulation ----------------
def test_simulation_known_values(client, auth):
    seed(client, auth, 4)
    body = {"horizon_months": 12, "extra_monthly_saving": 500000, "category_cuts": [{"category": "Food", "percent": 20}], "goal_target": 20000000}
    s = client.post(f"{API}/simulations/run", headers=auth, json=body).json()
    assert s["baseline"] == {"monthly_income": 10000000, "monthly_expense": 3837400, "monthly_savings": 6162600, "final_balance": 6162600 * 12}
    assert s["scenario"]["monthly_savings"] == 6162600 + 31500 + 500000 and s["scenario"]["category_cuts"][0]["monthly_saving"] == 31500
    assert s["difference"]["monthly_savings"] == 531500 and s["difference"]["final_balance"] == 531500 * 12 == s["difference"]["total_extra_saved"]
    assert s["goal"]["baseline_month"] == 4 and s["goal"]["scenario_month"] == 3
    assert len(s["series"]["scenario"]) == 12 and s["assumptions"]
    inc = client.post(f"{API}/simulations/run", headers=auth, json={"horizon_months": 6, "income_increase_pct": 10, "income_increase_amount": 100000}).json()
    assert inc["scenario"]["monthly_income"] == 10000000 + 1000000 + 100000
    ret = client.post(f"{API}/simulations/run", headers=auth, json={"horizon_months": 12, "annual_return_pct": 12, "starting_savings": 1000000}).json()
    # the starting-savings input is the SCENARIO opening balance; the baseline keeps the profile balance (0 here)
    assert ret["scenario"]["final_balance"] > 1000000 + 6162600 * 12 and ret["baseline"]["final_balance"] > 6162600 * 12 and any("illustrative" in a for a in ret["assumptions"])
    ef = client.post(f"{API}/simulations/run", headers=auth, json={"horizon_months": 24, "emergency_fund_months": 6}).json()
    assert ef["emergency_fund"]["baseline_target"] == 3837400 * 6 and ef["emergency_fund"]["baseline_month"] == 4


def test_simulation_loan_and_persistence(client, auth):
    body = {"horizon_months": 130, "base_monthly_income": 10000000, "base_monthly_expense": 6000000,
            "loan": {"principal": 100000000, "annual_rate_pct": 10, "tenure_months": 120, "extra_monthly_payment": 500000}, "save": True, "name": "Prepay"}
    s = client.post(f"{API}/simulations/run", headers=auth, json=body)
    assert s.status_code == 200, s.text
    s = s.json()
    assert s["loan"]["emi"] == 1321507 and s["loan"]["months"] == 120 and s["loan"]["with_extra_payment"]["months_saved"] > 0
    assert s["scenario"]["monthly_savings"] == s["baseline"]["monthly_savings"]
    assert s["difference"]["final_balance"] != 0
    assert len(client.get(f"{API}/simulations", headers=auth).json()["items"]) == 1
    assert client.delete(f"{API}/simulations/{s['id']}", headers=auth).status_code == 204
    q = client.get(f"{API}/simulations/loan", headers=auth, params={"principal": 100000000, "annual_rate_pct": 10, "tenure_months": 120}).json()
    assert q["emi"] == 1321507 and "with_extra_payment" not in q


def test_simulation_validation(client, auth):
    assert client.post(f"{API}/simulations/run", headers=auth, json={}).status_code == 422  # no data and no overrides
    ok = {"base_monthly_income": 100000, "base_monthly_expense": 50000}
    assert client.post(f"{API}/simulations/run", headers=auth, json=ok).status_code == 200
    assert client.post(f"{API}/simulations/run", headers=auth, json={**ok, "horizon_months": 0}).status_code == 422
    assert client.post(f"{API}/simulations/run", headers=auth, json={**ok, "horizon_months": 1000}).status_code == 422
    assert client.post(f"{API}/simulations/run", headers=auth, json={**ok, "extra_monthly_saving": -1}).status_code == 422
    assert client.post(f"{API}/simulations/run", headers=auth, json={**ok, "category_cuts": [{"category": "Food", "percent": 150}]}).status_code == 422


# ---------------- reports ----------------
def test_csv_and_excel_export(client, auth):
    seed(client, auth, 2)
    add_tx(client, auth, "2025-01-15", "=HYPERLINK(\"http://evil\")", 100, category="Other")
    c = client.get(f"{API}/reports/transactions.csv", headers=auth)
    assert c.status_code == 200 and c.headers["content-type"].startswith("text/csv") and "attachment" in c.headers["content-disposition"]
    lines = c.text.strip().splitlines()
    assert lines[0] == "Date,Description,Type,Category,Amount,Currency,Notes" and len(lines) == 1 + 15
    assert any(",30000.00,INR," in ln for ln in lines) and "'=HYPERLINK" in c.text  # formula injection neutralised
    f = client.get(f"{API}/reports/transactions.csv", headers=auth, params={"date_from": "2025-01-15", "date_to": "2025-01-15"}).text.strip().splitlines()
    assert len(f) == 2
    x = client.get(f"{API}/reports/transactions.xlsx", headers=auth)
    assert x.status_code == 200
    import io

    ws = load_workbook(io.BytesIO(x.content)).active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][0] == "Date" and len(rows) == 16 and any(float(r[4]) == 30000.0 for r in rows[1:])


def test_monthly_report_matches_dashboard_and_pdf(client, auth):
    last = seed(client, auth, 4)[-1]
    client.post(f"{API}/budgets", headers=auth, json={"period": last, "allocations": [{"category": "Groceries", "amount": 400000}]})
    client.post(f"{API}/goals", headers=auth, json={"name": "Laptop", "target_amount": 8000000, "current_amount": 2000000})
    rep = client.get(f"{API}/reports/monthly", headers=auth, params={"period": last}).json()
    dash = client.get(f"{API}/analytics/dashboard", headers=auth, params={"period": last}).json()
    assert rep["totals"] == dash["totals"] and rep["categories"] == dash["categories"]
    assert rep["goals"][0]["progress_pct"] == 25.0 and rep["budget"]["overspent_categories"] == ["Groceries"]
    assert any("Groceries" in i for i in rep["insights"]) and any("saved" in i for i in rep["insights"])
    pdf = client.get(f"{API}/reports/monthly.pdf", headers=auth, params={"period": last})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and len(pdf.content) > 2000 and pdf.headers["content-type"] == "application/pdf"
    empty = client.get(f"{API}/reports/monthly.pdf", headers=auth, params={"period": "2001-01"})
    assert empty.status_code == 200 and empty.content.startswith(b"%PDF")


def test_indexes_exist(client, mongo):
    client.get("/ready")
    from app.db import get_db

    db = get_db()
    assert db.users.index_information()["uniq_email"]["unique"]
    assert db.transactions.index_information()["uniq_user_fingerprint"]["unique"]
    assert db.budgets.index_information()["uniq_user_period"]["unique"]
    assert "user_date" in db.transactions.index_information() and "user_category" in db.transactions.index_information()


def test_older_statements_still_drive_forecast_and_optimizer(client, auth):
    import_statement(client, auth, make_statement(6, "2023-01"))
    f = client.get(f"{API}/forecasts", headers=auth).json()
    assert f["sufficient"] and f["history_to"] == "2023-06" and f["months"][0]["period"] == "2023-07" and f["stale_months"] > 12
    assert client.post(f"{API}/optimization/run", headers=auth, json={"months": 6}).status_code == 201
    assert client.get(f"{API}/goals/emergency-fund", headers=auth).status_code == 200


def test_simulation_never_assumes_zero_spending_from_profile_income_alone(client, auth):
    client.patch(f"{API}/profile", headers=auth, json={"monthly_income": 8500000})
    r = client.post(f"{API}/simulations/run", headers=auth, json={"horizon_months": 12})
    assert r.status_code == 422 and "spending" in r.json()["detail"]
    ok = client.post(f"{API}/simulations/run", headers=auth, json={"horizon_months": 12, "base_monthly_expense": 5000000})
    assert ok.status_code == 200 and ok.json()["baseline"] == {"monthly_income": 8500000, "monthly_expense": 5000000, "monthly_savings": 3500000, "final_balance": 42000000}


def test_category_trends_do_not_treat_months_before_first_data_as_zero(client, auth):
    add_tx(client, auth, "2026-10-03", "Rent October", 2200000, category="Housing")
    t = client.get(f"{API}/analytics/category-trends", headers=auth, params={"months": 6}).json()
    assert t["periods"] == ["2026-10"]  # window starts at the first month that has data
    h = t["categories"][0]
    assert (h["category"], h["values"], h["avg_per_month"], h["trend_per_month"], h["direction"]) == ("Housing", [2200000], 2200000, 0, "flat")
    add_tx(client, auth, "2026-08-03", "Rent August", 2000000, category="Housing")
    t = client.get(f"{API}/analytics/category-trends", headers=auth, params={"months": 6}).json()
    assert t["periods"] == ["2026-08", "2026-09", "2026-10"] and t["categories"][0]["values"] == [2000000, 0, 2200000]  # a real gap month inside the data is a real zero
