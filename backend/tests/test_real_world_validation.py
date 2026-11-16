"""Real-world validation against the SYNTHETIC statement in docs/test-data.

Every expected number here is computed independently in this file (Decimal / Fraction / plain Python) from the
ground-truth file - no application code is used to produce expectations. The API output (and what is stored in MongoDB)
is then compared with those expectations.
"""
import csv
import io
import json
import math
import re
import zlib
from base64 import a85decode
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path
from statistics import median

import pytest
from openpyxl import Workbook, load_workbook

from tests.conftest import API, register

DATA = Path(__file__).resolve().parents[2] / "docs" / "test-data"
STATEMENT = DATA / "sample_bank_statement.csv"
EXPECTED = json.loads((DATA / "expected_figures.json").read_text(encoding="utf-8"))
TRUTH = [dict(r, line=int(r["line"]), amount_paise=int(r["amount_paise"])) for r in csv.DictReader((DATA / "sample_bank_statement.truth.csv").open(encoding="utf-8"))]
MONTHS = sorted(EXPECTED["months"])  # 2026-08, 2026-09, 2026-10
FIXED = {"Housing", "Utilities", "Insurance", "Loan Repayment", "Subscriptions"}
ESSENTIAL = {"Housing", "Groceries", "Healthcare", "Utilities", "Insurance", "Loan Repayment", "Education"}


def raw_rows():
    """The uploaded file parsed with the csv module (no app code): [(date, narration, debit_paise, credit_paise)]."""
    out = []
    for r in csv.DictReader(STATEMENT.open(encoding="utf-8")):
        d = datetime.strptime(r["Date"], "%d/%m/%y").date()
        deb, cred = r["Withdrawal Amt."].replace(",", ""), r["Deposit Amt."].replace(",", "")
        out.append((d, r["Narration"], int(Decimal(deb) * 100) if deb else 0, int(Decimal(cred) * 100) if cred else 0))
    return out


RAW = raw_rows()


def upload(client, auth, content: bytes, filename="s.csv", mapping=None):
    data = {"mapping": json.dumps(mapping)} if mapping else None
    return client.post(f"{API}/imports/preview", headers=auth, files={"file": (filename, content)}, data=data)


def import_sample(client, auth):
    p = upload(client, auth, STATEMENT.read_bytes(), "sample_bank_statement.csv")
    assert p.status_code == 200, p.text
    c = client.post(f"{API}/imports/{p.json()['import_id']}/commit", headers=auth, json={})
    assert c.status_code == 200, c.text
    return p.json(), c.json()


@pytest.fixture()
def loaded(client, auth):
    prev, res = import_sample(client, auth)
    return {"preview": prev, "result": res}


def dash(client, auth, period):
    return client.get(f"{API}/analytics/dashboard", headers=auth, params={"period": period}).json()


# =============================== PHASE 2: import ===============================
def assert_rows_match_truth(rows):
    assert len(rows) == len(TRUTH) == 95
    for got, t in zip(rows, TRUTH, strict=True):
        ctx = f"{t['narration']!r}"
        assert got["date"] == t["date"], ctx
        assert got["amount"] == t["amount_paise"], ctx
        assert got["type"] == t["expected_type"], ctx
        assert got["category"] == t["expected_category"], ctx


def test_sample_statement_auto_detected_and_every_row_matches_truth(loaded):
    p = loaded["preview"]
    assert p["needs_mapping"] is False and p["summary"]["errors"] == 0 and p["summary"]["new"] == 95
    assert p["mapping"]["debit"] == "Withdrawal Amt." and p["mapping"]["credit"] == "Deposit Amt." and p["mapping"]["description"] == "Narration"
    assert_rows_match_truth(p["rows"])
    # independent totals of the preview summary
    inc = sum(t["amount_paise"] for t in TRUTH if t["expected_type"] == "income")
    exp = sum(t["amount_paise"] for t in TRUTH if t["expected_type"] == "expense")
    ref = sum(t["amount_paise"] for t in TRUTH if t["expected_type"] == "refund")
    assert (p["summary"]["income_total"], p["summary"]["expense_total"], p["summary"]["refund_total"], p["summary"]["transfers"]) == (inc, exp, ref, EXPECTED["type_counts"]["transfer"])
    assert loaded["result"]["inserted"] == 95


def test_transfers_never_count_as_income_or_expense_and_trap_is_not_paired(loaded):
    types = {(t["narration"]): t["expected_type"] for t in TRUTH}
    got = {r["description"]: r["type"] for r in loaded["preview"]["rows"]}
    for narration, typ in types.items():
        assert got[narration] == typ
    # same-day, same-amount NEFT credit from a friend must stay income (not paired with the 22,000 rent)
    assert got["NEFT CR-FRIEND AMIT-REPAYMENT OF LOAN"] == "income" and got["NEFT DR-LANDLORD R KUMAR-RENT SEP"] == "expense"
    assert got["IMPS-SELF TRANSFER TO OWN A/C WALLET"] == got["IMPS-SELF TRANSFER FROM OWN A/C WALLET"] == "transfer"


def test_mongodb_storage_is_exact(client, auth, loaded):
    from app.db import get_db

    db = get_db()
    docs = list(db.transactions.find({}))
    assert len(docs) == 95
    uid = db.users.find_one({"email": "alice@example.com"})["_id"]
    for d in docs:
        assert d["user_id"] == uid
        assert type(d["amount"]) is int and d["amount"] > 0  # integer paise, never float, never signed
        assert d["date"].tzinfo is not None and d["date"].utcoffset() == timedelta(0) and (d["date"].hour, d["date"].minute) == (0, 0)
        assert d["type"] in {"income", "expense", "refund", "transfer"} and d["source"] == "import" and d["currency"] == "INR"
        assert d["fingerprint"] and d["normalized_description"] and "_id" in d
        assert not any(isinstance(v, float) for v in d.values())
    assert len({d["fingerprint"] for d in docs}) == 95  # even the identical twin charges have distinct fingerprints
    stored = sorted((d["date"].date().isoformat(), d["description"], d["amount"], d["type"], d["category"]) for d in docs)
    truth = sorted((t["date"], t["narration"], t["amount_paise"], t["expected_type"], t["expected_category"]) for t in TRUTH)
    assert stored == truth
    imp = db.bank_imports.find_one({"status": "committed"})
    assert imp["result"]["inserted"] == 95 and "rows" not in imp  # staged rows (bank data) are removed after commit
    assert db.transactions.count_documents({"user_id": uid, "description": {"$regex": "SWIGGY.*ORDER 6"}}) == 2


def test_reimport_creates_no_duplicates(client, auth, loaded):
    again = upload(client, auth, STATEMENT.read_bytes()).json()
    assert again["summary"]["duplicates"] == 95 and again["summary"]["new"] == 0 and again["already_imported"] is True
    c = client.post(f"{API}/imports/{again['import_id']}/commit", headers=auth, json={}).json()
    assert c["inserted"] == 0 and c["skipped_duplicates"] == 95
    assert client.get(f"{API}/transactions", headers=auth).json()["total"] == 95
    # a re-export with one extra new row and everything else identical only adds that row
    extra = STATEMENT.read_text(encoding="utf-8") + '31/10/26,UPI-NEW SHOP,000000999999,31/10/26,150.00,,"53,587.50"\n'
    p = upload(client, auth, extra.encode()).json()
    assert (p["summary"]["new"], p["summary"]["duplicates"]) == (1, 95)


def _format_variants():
    """Same 95 transactions rendered the way different banks/exports render them."""
    ds = lambda d, f: d.strftime(f)  # noqa: E731
    rm = lambda p: f"{Decimal(p) / 100:.2f}"  # noqa: E731
    v = {}
    # ICICI-style: dd-mm-yyyy, punctuation/units in headers
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["S No.", "Transaction Date", "Transaction Remarks", "Withdrawal Amount (INR )", "Deposit Amount (INR )", "Balance (INR )"])
    for i, (d, n, db_, cr) in enumerate(RAW, 1):
        w.writerow([i, ds(d, "%d-%m-%Y"), n, rm(db_) if db_ else "", rm(cr) if cr else "", "0.00"])
    v["icici"] = (out.getvalue().encode(), "icici.csv", None)
    # SBI-style: "01 Aug 2026", Debit/Credit with Rs. prefix and commas
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Txn Date", "Value Date", "Description", "Ref No./Cheque No.", "Debit", "Credit", "Balance"])
    for d, n, db_, cr in RAW:
        w.writerow([ds(d, "%d %b %Y"), ds(d, "%d %b %Y"), n, "", f"Rs. {db_ / 100:,.2f}" if db_ else "", f"Rs. {cr / 100:,.2f}" if cr else "", ""])
    v["sbi"] = (out.getvalue().encode(), "sbi.csv", None)
    # single signed amount column, ISO dates
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Description", "Amount"])
    for d, n, db_, cr in RAW:
        w.writerow([d.isoformat(), n, rm(cr) if cr else "-" + rm(db_)])
    v["signed"] = (out.getvalue().encode(), "signed.csv", None)
    # unsigned amount + Dr/Cr indicator column
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Date", "Particulars", "Amount", "Dr/Cr"])
    for d, n, db_, cr in RAW:
        w.writerow([ds(d, "%d/%m/%Y"), n, rm(cr or db_), "CR" if cr else "DR"])
    v["drcr"] = (out.getvalue().encode(), "drcr.csv", None)
    # semicolon delimited, parenthesised negatives, Windows-1252 bytes
    lines = ["Date;Narration;Amount"] + [f"{d.isoformat()};{n};{rm(cr) if cr else '(' + rm(db_) + ')'}" for d, n, db_, cr in RAW]
    v["semicolon"] = ("\n".join(lines).encode("cp1252"), "semi.csv", None)
    # unrecognised headers: user supplies the mapping
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Posted", "Details of transaction", "Out", "In"])
    for d, n, db_, cr in RAW:
        w.writerow([ds(d, "%d/%m/%Y"), n, rm(db_) if db_ else "", rm(cr) if cr else ""])
    v["mapped"] = (out.getvalue().encode(), "odd.csv", {"date": "Posted", "description": "Details of transaction", "debit": "Out", "credit": "In", "dayfirst": True})
    # XLSX with 3 preamble rows, real date cells and float amounts
    wb = Workbook()
    ws = wb.active
    ws.append(["Example Bank Ltd - Statement of Account (SYNTHETIC)"])
    ws.append(["Account: XXXX4471", "Period: Aug-Oct 2026"])
    ws.append([])
    ws.append(["Date", "Narration", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"])
    for d, n, db_, cr in RAW:
        ws.append([datetime(d.year, d.month, d.day), n, db_ / 100 if db_ else None, cr / 100 if cr else None, 0])
    buf = io.BytesIO()
    wb.save(buf)
    v["xlsx"] = (buf.getvalue(), "statement.xlsx", None)
    return v


@pytest.mark.parametrize("fmt", ["icici", "sbi", "signed", "drcr", "semicolon", "mapped", "xlsx"])
def test_bank_formats_all_import_identically(client, auth, fmt):
    content, name, mapping = _format_variants()[fmt]
    r = upload(client, auth, content, name, mapping)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["needs_mapping"] is False and p["summary"]["errors"] == 0 and p["summary"]["valid_rows"] == 95, p.get("errors")
    assert_rows_match_truth(p["rows"])  # dates, paise, debit/credit direction, type and category all equal the truth


def test_unrecognised_headers_ask_for_mapping(client, auth):
    content, name, _ = _format_variants()["mapped"]
    p = upload(client, auth, content, name).json()
    assert p["needs_mapping"] is True and p["headers"] == ["Posted", "Details of transaction", "Out", "In"] and p["import_id"] is None


def test_error_messages_are_understandable(client, auth):
    cases = [
        (b"", "empty.csv", 400, "empty"),
        (b"hello", "notes.txt", 422, "csv"),
        (b"PK\x03\x04junk", "fake.xlsx", 422, "xlsx"),
        (b"Date,Description,Debit\n", "headeronly.csv", 422, "no transaction rows"),
        (b"Date,Description,Debit,Credit\n2026-01-01,Shop,abc,\n", "bad.csv", 422, "No valid transactions"),
    ]
    for content, fname, status, word in cases:
        r = upload(client, auth, content, fname)
        assert r.status_code == status, (fname, r.text)
        detail = r.json()["detail"]
        msg = detail if isinstance(detail, str) else detail["message"]
        assert word.lower() in msg.lower() and "Traceback" not in r.text and "File \"" not in r.text, (fname, msg)


def test_large_file_within_limits_reconciles(client, auth):
    n, rows, expect_exp, expect_inc = 5000, ["Date,Description,Debit,Credit"], 0, 0
    for i in range(n):
        d = date(2026, 1 + i % 10, 1 + i % 28).isoformat()
        if i % 50 == 0:
            amt = 50000 + i
            rows.append(f"{d},Salary ACME {i},,{amt}.00")
            expect_inc += amt * 100
        else:
            paise = 1000 + (i * 37) % 90000
            rows.append(f"{d},Shop {i % 70} item ref{i},{paise // 100}.{paise % 100:02d},")
            expect_exp += paise
    p = upload(client, auth, "\n".join(rows).encode()).json()
    assert p["summary"]["valid_rows"] == n
    c = client.post(f"{API}/imports/{p['import_id']}/commit", headers=auth, json={}).json()
    assert c["inserted"] == n
    from app.db import get_db

    db = get_db()
    agg = {a["_id"]: a["t"] for a in db.transactions.aggregate([{"$group": {"_id": "$type", "t": {"$sum": "$amount"}}}])}
    assert agg == {"income": expect_inc, "expense": expect_exp}
    y = client.get(f"{API}/analytics/yearly/2026", headers=auth).json()
    assert y["totals"]["income"] == expect_inc and y["totals"]["net_expense"] == expect_exp


# =============================== PHASE 3: calculations ===============================
@pytest.mark.parametrize("period", MONTHS)
def test_dashboard_matches_independent_figures(client, auth, loaded, period):
    e = EXPECTED["months"][period]
    d = dash(client, auth, period)
    t = d["totals"]
    assert (t["income"], t["gross_expense"], t["refunds"], t["net_expense"], t["net_cash_flow"]) == (e["income"], e["gross_expense"], e["refunds"], e["net_expense"], e["net_cash_flow"])
    assert t["savings_pct"] == pytest.approx(e["savings_pct"], abs=0.005)
    assert {c["category"]: c["amount"] for c in d["categories"]} == e["categories"]
    assert sum(c["amount"] for c in d["categories"]) == t["net_expense"]  # categories reconcile with the headline number
    assert {c["category"]: c["amount"] for c in d["income_categories"]} == e["income_categories"]
    assert (d["fixed_variable"]["fixed"], d["fixed_variable"]["variable"]) == (e["fixed"], e["variable"])
    assert t["transaction_count"] == e["rows"] - sum(1 for x in TRUTH if x["expected_type"] == "transfer" and x["date"][:7] == period)


def test_savings_pct_is_exact_to_two_decimals(client, auth, loaded):
    for p in MONTHS:
        e = EXPECTED["months"][p]
        want = float((Fraction(e["net_cash_flow"] * 100, e["income"])).limit_denominator(10**9))
        assert dash(client, auth, p)["totals"]["savings_pct"] == round(want, 2)


def test_month_over_month_trend_and_comparisons(client, auth, loaded):
    d = dash(client, auth, "2026-10")
    assert [(x["period"], x["income"], x["net_expense"], x["net_cash_flow"]) for x in d["trend"]] == [
        (p, EXPECTED["months"][p]["income"], EXPECTED["months"][p]["net_expense"], EXPECTED["months"][p]["net_cash_flow"]) for p in MONTHS]
    c = d["vs_previous_month"]
    o, s = EXPECTED["months"]["2026-10"], EXPECTED["months"]["2026-09"]
    assert c["expense_change"] == o["net_expense"] - s["net_expense"] and c["income_change"] == o["income"] - s["income"]
    assert c["savings_change"] == o["net_cash_flow"] - s["net_cash_flow"]
    assert c["expense_change_pct"] == pytest.approx((o["net_expense"] - s["net_expense"]) * 100 / s["net_expense"], abs=0.005)
    assert d["vs_same_month_last_year"] is None  # no 2025 data: no invented comparison
    y = client.get(f"{API}/analytics/yearly/2026", headers=auth).json()
    assert y["totals"]["income"] == sum(EXPECTED["months"][p]["income"] for p in MONTHS) and y["totals"]["net_expense"] == sum(EXPECTED["months"][p]["net_expense"] for p in MONTHS)
    assert [m["period"] for m in y["monthly"]] == MONTHS


def test_refund_offsets_original_purchase_and_transfers_ignored(client, auth, loaded):
    sep = dash(client, auth, "2026-09")
    shopping = next(c for c in sep["categories"] if c["category"] == "Shopping")["amount"]
    assert shopping == (89900 + 174000 - 129900)  # Sep purchases minus the Aug-order refund, in paise
    assert sep["totals"]["refunds"] == 129900
    ccp = sum(t["amount_paise"] for t in TRUTH if t["expected_type"] == "transfer")
    assert ccp > 0 and sum(EXPECTED["months"][p]["gross_expense"] for p in MONTHS) == sum(t["amount_paise"] for t in TRUTH if t["expected_type"] == "expense")


def test_budget_usage_matches_independent_calculation(client, auth, loaded):
    e = EXPECTED["months"]["2026-10"]["categories"]
    alloc = {"Groceries": 900000, "Food": 600000, "Transport": 500000, "Housing": 2200000, "Healthcare": 200000, "Entertainment": 100000}
    r = client.post(f"{API}/budgets", headers=auth, json={"period": "2026-10", "allocations": [{"category": k, "amount": v} for k, v in alloc.items()]})
    assert r.status_code == 201 and r.json()["total"] == sum(alloc.values())
    p = client.get(f"{API}/budgets/2026-10/performance", headers=auth).json()
    rows = {x["category"]: x for x in p["rows"]}
    for cat, bud in alloc.items():
        act = e.get(cat, 0)
        x = rows[cat]
        assert (x["budget"], x["actual"], x["remaining"], x["over_budget"]) == (bud, act, bud - act, act > bud)
        assert x["utilization_pct"] == round(act * 100 / bud, 2)
    assert rows["Shopping"]["unbudgeted"] is True and rows["Shopping"]["actual"] == e["Shopping"]
    total_actual = EXPECTED["months"]["2026-10"]["net_expense"]
    assert (p["total_budget"], p["total_actual"], p["remaining"]) == (sum(alloc.values()), total_actual, sum(alloc.values()) - total_actual)
    assert set(p["overspent_categories"]) == {c for c, b in alloc.items() if e.get(c, 0) > b}
    assert "Healthcare" in p["overspent_categories"]  # the Rs 9,800 hospital bill


def test_goal_progress_and_required_contribution(client, auth, loaded):
    today = datetime.now(timezone.utc).date()
    target_date = today + timedelta(days=200)
    r = client.post(f"{API}/goals", headers=auth, json={"name": "Laptop", "target_amount": 8000000, "current_amount": 1999999, "target_date": target_date.isoformat(), "monthly_contribution": 700000}).json()
    remaining = 8000000 - 1999999
    months_left = math.ceil(Fraction(200) / Fraction(304375, 10000))  # independent: 30.4375-day months, rounded up
    assert r["remaining"] == remaining and r["months_remaining"] == months_left
    assert r["required_monthly_contribution"] == math.ceil(Fraction(remaining, months_left))
    assert r["progress_pct"] == round(1999999 * 100 / 8000000, 2)
    n = math.ceil(Fraction(remaining, 700000))
    y, m = divmod(today.year * 12 + today.month - 1 + n, 12)
    import calendar

    exp_date = date(y, m + 1, min(today.day, calendar.monthrange(y, m + 1)[1]))
    assert r["estimated_completion"] == exp_date.isoformat() and r["on_track"] == (exp_date <= target_date)
    c = client.post(f"{API}/goals/{r['id']}/contributions", headers=auth, json={"amount": 1}).json()
    assert c["current_amount"] == 2000000 and c["progress_pct"] == 25.0


def test_emergency_fund_matches_independent_average(client, auth, loaded):
    client.patch(f"{API}/profile", headers=auth, json={"current_savings": 12000000})
    ess = [EXPECTED["months"][p]["essential"] for p in MONTHS]
    avg = sum(ess) // 3
    e = client.get(f"{API}/goals/emergency-fund", headers=auth, params={"months": 6, "monthly_contribution": 1500000}).json()
    assert e["average_monthly_expense"] == avg and e["history_months_used"] == 3
    assert e["target"] == avg * 6 and e["gap"] == max(0, avg * 6 - 12000000)
    assert e["months_to_goal"] == math.ceil(Fraction(e["gap"], 1500000)) and e["coverage_months"] == round(12000000 / avg, 1)
    tot = sum(EXPECTED["months"][p]["net_expense"] for p in MONTHS) // 3
    assert client.get(f"{API}/goals/emergency-fund", headers=auth, params={"basis": "total"}).json()["average_monthly_expense"] == tot


# ---- recurring ----
def _truth_by(keyword):
    return [t for t in TRUTH if keyword in t["narration"].upper()]


MUST_BE_RECURRING = {"LANDLORD": "Housing", "HDFC BANK LOAN EMI": "Loan Repayment", "HEALTH INSURANCE": "Insurance", "ACT FIBERNET": "Utilities",
                     "BESCOM": "Utilities", "NETFLIX": "Subscriptions", "SPOTIFY": "Subscriptions", "JIO PREPAID": "Utilities"}
MUST_NOT = ["SWIGGY", "ZOMATO", "APPLE STORE", "MAKEMYTRIP", "CITY HOSPITAL", "CREDIT CARD PAYMENT", "SELF TRANSFER", "SALARY", "CAFE COFFEE"]


def test_recurring_payments_detected_with_independent_amounts(client, auth, loaded):
    assert client.post(f"{API}/recurring/detect", headers=auth).status_code == 200
    items = client.get(f"{API}/recurring", headers=auth).json()
    names = [i["name"].upper() for i in items["items"]]
    expected_total = 0
    for kw, cat in MUST_BE_RECURRING.items():
        match = [i for i in items["items"] if kw in i["name"].upper()]
        assert len(match) == 1, f"{kw} not detected exactly once"
        amts = [t["amount_paise"] for t in _truth_by(kw)]
        assert len(amts) == 3 and match[0]["frequency"] == "monthly" and match[0]["amount"] == int(median(amts)) and match[0]["category"] == cat
        assert match[0]["evidence"]["occurrences"] == 3 and match[0]["confidence"] >= 0.5 and match[0]["kind"] == "bill_or_subscription"
        expected_total += match[0]["estimated_monthly"]
    for kw in MUST_NOT:
        assert not any(kw in n for n in names), f"{kw} must not be listed as recurring"
    assert expected_total == 22000 * 100 + 650000 + 185000 + 99900 + 184000 + 64900 + 11900 + 29900  # independent sum of the medians
    assert items["estimated_monthly_total"] == expected_total and items["estimated_annual_total"] == expected_total * 12
    elec = next(i for i in items["items"] if "BESCOM" in i["name"].upper())
    assert elec["evidence"]["amount_consistency"] < 0.5 <= elec["confidence"]  # variable bill: detected, with honest evidence


# ---- anomalies ----
def test_anomalies_flag_exactly_the_unusual_items(client, auth, loaded):
    a = client.get(f"{API}/analytics/anomalies", headers=auth).json()
    big = {x["amount"] for x in a["large_transactions"]}
    assert {6490000, 680000} <= big  # Rs 64,900 Apple purchase, Rs 6,800 party order
    assert not big & {200000, 210000, 195000, 2200000, 650000, 8500000}  # fuel, rent, EMI, salary are normal for their payee
    spikes = {s["category"] for s in a["category_spikes"]}
    assert {"Healthcare", "Travel", "Shopping"} <= spikes and "Housing" not in spikes and "Groceries" not in spikes
    hc = next(s for s in a["category_spikes"] if s["category"] == "Healthcare")
    prior = [EXPECTED["months"][p]["categories"].get("Healthcare", 0) for p in ("2026-08", "2026-09")]
    assert hc["previous_average"] == sum(prior) // 2 and hc["amount"] == EXPECTED["months"]["2026-10"]["categories"]["Healthcare"]
    assert hc["increase_pct"] == pytest.approx((hc["amount"] - sum(prior) / 2) * 100 / (sum(prior) / 2), abs=0.01)
    dups = [d for d in a["possible_duplicates"] if d["amount"] == 48600]
    assert len(dups) == 1 and dups[0]["dates"] == ["2026-10-18", "2026-10-18"]
    assert "not necessarily wrong or fraudulent" in a["disclaimer"]


# ---- forecast ----
def _wma_forecast(values, h):
    v = [Fraction(x) for x in values]
    base = sum(v[-3:][i] * (i + 1) for i in range(3)) / 6  # n == 3: no trend term (needs >= 4 months)
    mean = sum(v) / len(v)
    sd = math.sqrt(float(sum((x - mean) ** 2 for x in v) / (len(v) - 1)))
    return float(base), sd


def test_forecast_matches_independent_calculation(client, auth, loaded):
    client.patch(f"{API}/profile", headers=auth, json={"current_savings": 5000000})
    f = client.get(f"{API}/forecasts", headers=auth, params={"horizon": 3}).json()
    assert f["sufficient"] and f["history_months"] == 3 and f["confidence"] == "low" and f["history_from"] == "2026-08" and f["history_to"] == "2026-10"
    assert [m["period"] for m in f["months"]] == ["2026-11", "2026-12", "2027-01"]
    exp_v = [EXPECTED["months"][p]["net_expense"] for p in MONTHS]
    inc_v = [EXPECTED["months"][p]["income"] for p in MONTHS]
    eb, esd = _wma_forecast(exp_v, 3)
    ib, isd = _wma_forecast(inc_v, 3)
    esd_i, isd_i = round(esd), round(isd)
    cum = 5000000
    for i, m in enumerate(f["months"], start=1):
        assert abs(m["expense"]["mid"] - round(eb)) <= 1 and abs(m["income"]["mid"] - round(ib)) <= 1
        assert abs(m["expense"]["low"] - max(0, round(eb - esd))) <= 2 and abs(m["expense"]["high"] - round(eb + esd)) <= 2
        assert m["net_savings"] == m["income"]["mid"] - m["expense"]["mid"]
        cum += m["net_savings"]
        assert m["cumulative_savings"] == cum
        spread = round(math.sqrt(isd_i**2 + esd_i**2) * math.sqrt(i))
        assert abs((m["cumulative_high"] - m["cumulative_savings"]) - spread) <= 1 and abs((m["cumulative_savings"] - m["cumulative_low"]) - spread) <= 1
    assert f["expense_trend_per_month"] == 0 and f["avg_projected_monthly_savings"] == sum(m["net_savings"] for m in f["months"]) // 3
    assert any("Based on 3 completed" in line for line in f["method"])


def test_forecast_refuses_with_two_months(client, auth):
    rows = ["Date,Description,Debit,Credit", "2026-09-01,Salary,,50000.00", "2026-09-02,Rent,20000.00,", "2026-10-01,Salary,,50000.00", "2026-10-02,Rent,20000.00,"]
    p = upload(client, auth, "\n".join(rows).encode()).json()
    client.post(f"{API}/imports/{p['import_id']}/commit", headers=auth, json={})
    f = client.get(f"{API}/forecasts", headers=auth).json()
    assert f["sufficient"] is False and f["history_months"] == 2 and "months" not in f


# ---- optimisation ----
CLASS_BP = {"conservative": (500, {"discretionary": 1500, "semi": 500, "essential": 0}), "balanced": (1000, {"discretionary": 3000, "semi": 1000, "essential": 500}),
            "aggressive": (1800, {"discretionary": 5000, "semi": 2000, "essential": 1000})}
PAIN = {"discretionary": 1.0, "semi": 3.0, "essential": 8.0}


def _klass(c):
    return "semi" if c == "Transport" else "essential" if c in ESSENTIAL else "discretionary"


def test_optimizer_constraints_and_optimality_independently(client, auth, loaded):
    avg = {}
    for p in MONTHS:
        for c, v in EXPECTED["months"][p]["categories"].items():
            avg[c] = avg.get(c, 0) + v
    avg = {c: v // 3 for c, v in avg.items() if v // 3 > 0}
    variable = {c: v for c, v in avg.items() if c not in FIXED}
    r = client.post(f"{API}/optimization/run", headers=auth, json={"months": 3, "min_spend": {"Entertainment": 70000}})
    assert r.status_code == 201
    rec = r.json()
    assert rec["current_variable_total"] == sum(variable.values()) and {f["category"]: f["monthly"] for f in rec["fixed_categories"]} == {c: v for c, v in avg.items() if c in FIXED}
    prev_saved = -1
    for name, (target_bp, cuts) in CLASS_BP.items():
        sc = rec["scenarios"][name]
        floors = {}
        for c, cur in variable.items():
            fl = max(cur - cur * cuts[_klass(c)] // 10000, min(70000 if c == "Entertainment" else 0, cur))
            floors[c] = fl
        target = sum(variable.values()) * target_bp // 10000
        feasible = sum(variable[c] - floors[c] for c in variable)
        got = {c["category"]: c for c in sc["categories"]}
        assert set(got) == set(variable)
        for c, cur in variable.items():
            assert got[c]["current"] == cur and floors[c] <= got[c]["suggested"] <= cur, (name, c)
        achieved = sum(cur - got[c]["suggested"] for c, cur in variable.items())
        assert sc["achieved_savings"] == achieved and sc["target_savings"] == target and sc["annual_savings"] == achieved * 12
        assert achieved >= min(target, feasible) and sc["valid"] is True
        assert achieved > prev_saved
        prev_saved = achieved
        # independent optimum: minimise sum w*c*r^2 with the same constraints via exact water-filling (bisection)
        cs = {c: variable[c] for c in variable}
        cap = {c: (variable[c] - floors[c]) for c in variable}
        w = {c: PAIN[_klass(c)] for c in variable}

        def spend(lam, cs=cs, cap=cap, w=w):
            return sum(min(cap[c], lam / w[c] * cs[c]) for c in cs)

        want = min(target, feasible)
        lo, hi = 0.0, 1e7
        for _ in range(200):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if spend(mid) < want else (lo, mid)
        ref_red = {c: min(cap[c], hi / w[c] * cs[c]) for c in cs}
        ref_cost = sum(w[c] * ref_red[c] ** 2 / cs[c] for c in cs)
        app_cost = sum(w[c] * (cs[c] - got[c]["suggested"]) ** 2 / cs[c] for c in cs)
        assert app_cost <= ref_cost * 1.01 + 1e3, (name, app_cost, ref_cost)  # app's allocation is (near-)optimal
    assert {c["category"]: c["suggested"] for c in rec["scenarios"]["balanced"]["categories"]}["Entertainment"] >= 70000
    assert client.get(f"{API}/budgets", headers=auth).json()["items"] == []  # nothing applied automatically


# ---- simulation + EMI ----
def hu_dec(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def test_simulation_matches_independent_decimal_model(client, auth, loaded):
    inc = sum(EXPECTED["months"][p]["income"] for p in MONTHS) // 3
    exp = sum(EXPECTED["months"][p]["net_expense"] for p in MONTHS) // 3
    food = sum(EXPECTED["months"][p]["categories"].get("Food", 0) for p in MONTHS) // 3
    body = {"horizon_months": 24, "extra_monthly_saving": 300000, "income_increase_pct": 7.5, "income_increase_amount": 25000,
            "category_cuts": [{"category": "Food", "percent": 20}], "annual_return_pct": 9, "starting_savings": 1000000, "goal_target": 50000000}
    s = client.post(f"{API}/simulations/run", headers=auth, json=body).json()
    cut = hu_dec(Decimal(food) * Decimal("0.20"))
    inc1 = inc + 25000 + hu_dec(Decimal(inc) * Decimal("0.075"))
    monthly = inc1 - (exp - cut) + 300000
    assert s["baseline"] == {"monthly_income": inc, "monthly_expense": exp, "monthly_savings": inc - exp, "final_balance": s["baseline"]["final_balance"]}
    assert s["scenario"]["monthly_savings"] == monthly and s["scenario"]["category_cuts"][0]["monthly_saving"] == cut
    r = Decimal("9") / 1200

    profile_savings = int(client.get(f"{API}/profile", headers=auth).json().get("current_savings") or 0)  # the baseline opens with the real profile balance

    def run(m, start=1000000):  # scenario opens with the starting_savings input (1,000,000)
        b, out = start, []
        for _ in range(24):
            b = hu_dec(Decimal(b) * (1 + r)) + m
            out.append(b)
        return out

    assert s["series"]["scenario"] == run(monthly) and s["series"]["baseline"] == run(inc - exp, profile_savings)
    assert s["difference"]["final_balance"] == run(monthly)[-1] - run(inc - exp, profile_savings)[-1]
    assert s["difference"]["starting_savings"] == 1000000 - profile_savings
    first = next((i + 1 for i, b in enumerate(run(monthly)) if b >= 50000000), None)
    assert s["goal"]["scenario_month"] == first


def _emi_closed_form(P, annual, n):
    r = Decimal(str(annual)) / 1200
    f = (1 + r) ** n
    return hu_dec(Decimal(P) * r * f / (f - 1))


def _emi_bisection(P, annual, n):
    """Find the level payment that amortises P to ~0 in n months by bisection (independent of the closed form)."""
    r = Decimal(str(annual)) / 1200
    lo, hi = Decimal(0), Decimal(P)
    for _ in range(120):
        mid = (lo + hi) / 2
        bal = Decimal(P)
        for _ in range(n):
            bal = bal * (1 + r) - mid
        lo, hi = (mid, hi) if bal > 0 else (lo, mid)
    return hu_dec(hi)


@pytest.mark.parametrize("P,annual,n", [(100000000, 10, 120), (25000000, 8.35, 187), (7500000, 12.5, 24), (50000000, 0.5, 360), (123456789, 9.99, 61)])
def test_emi_and_amortization_independent(client, auth, P, annual, n):
    q = client.get(f"{API}/simulations/loan", headers=auth, params={"principal": P, "annual_rate_pct": annual, "tenure_months": n}).json()
    assert abs(q["emi"] - _emi_closed_form(P, annual, n)) <= 0 and abs(q["emi"] - _emi_bisection(P, annual, n)) <= 1
    assert q["months"] == n
    # independent amortisation with the app's published rounding rule: interest rounded half-up each month, last payment clears the balance
    r, bal, tot_int = Decimal(str(annual)) / 1200, P, 0
    for k in range(1, n + 1):
        interest = hu_dec(Decimal(bal) * r)
        pay = bal + interest if k == n else q["emi"]
        bal -= pay - interest
        tot_int += interest
    assert bal == 0 and q["total_interest"] == tot_int and q["total_paid"] == P + tot_int
    first = q["first_12_months"][0]
    assert first["interest"] == hu_dec(Decimal(P) * r) and first["principal"] + first["interest"] == first["payment"]
    ex = client.get(f"{API}/simulations/loan", headers=auth, params={"principal": P, "annual_rate_pct": annual, "tenure_months": n, "extra_monthly_payment": q["emi"] // 5}).json()["with_extra_payment"]
    assert ex["months"] < n and ex["interest_saved"] > 0 and ex["months_saved"] == n - ex["months"]


# ---- reports ----
def _pdf_text(pdf: bytes) -> str:
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        blob = m.group(1).strip()
        if blob.endswith(b"~>"):  # ReportLab: ASCII85 + Flate page streams
            blob = blob[:-2]
        try:
            out.append(zlib.decompress(a85decode(blob)).decode("latin-1"))
        except Exception:  # noqa: S112  (non-text streams)
            continue
    return "".join(out)


def test_reports_csv_xlsx_pdf_match_independent_figures(client, auth, loaded):
    c = client.get(f"{API}/reports/transactions.csv", headers=auth)
    rows = list(csv.DictReader(io.StringIO(c.text)))
    assert len(rows) == 95
    sums = {}
    for r in rows:
        sums[r["Type"]] = sums.get(r["Type"], Decimal(0)) + Decimal(r["Amount"])
    for typ in ("income", "expense", "refund", "transfer"):
        assert sums[typ] * 100 == sum(t["amount_paise"] for t in TRUTH if t["expected_type"] == typ)
    assert all(re.fullmatch(r"\d+\.\d{2}", r["Amount"]) for r in rows)  # exact 2-decimal paise strings
    assert sorted((r["Date"], r["Description"], r["Category"]) for r in rows) == sorted((t["date"], t["narration"], t["expected_category"]) for t in TRUTH)
    f = client.get(f"{API}/reports/transactions.csv", headers=auth, params={"date_from": "2026-10-01", "date_to": "2026-10-31"}).text
    assert len(list(csv.DictReader(io.StringIO(f)))) == EXPECTED["months"]["2026-10"]["rows"]
    ws = load_workbook(io.BytesIO(client.get(f"{API}/reports/transactions.xlsx", headers=auth).content)).active
    xr = list(ws.iter_rows(values_only=True))[1:]
    assert len(xr) == 95 and sum(Decimal(str(r[4])) for r in xr if r[2] == "income") * 100 == sum(t["amount_paise"] for t in TRUTH if t["expected_type"] == "income")
    rep = client.get(f"{API}/reports/monthly", headers=auth, params={"period": "2026-10"}).json()
    e = EXPECTED["months"]["2026-10"]
    assert (rep["totals"]["income"], rep["totals"]["net_expense"], rep["totals"]["net_cash_flow"]) == (e["income"], e["net_expense"], e["net_cash_flow"])
    pdf = client.get(f"{API}/reports/monthly.pdf", headers=auth, params={"period": "2026-10"})
    assert pdf.content.startswith(b"%PDF")
    text = _pdf_text(pdf.content)
    assert "Monthly Financial Report - 2026-10" in text
    # figures printed in the PDF (Indian digit grouping) = independent expectations for October
    assert EXPECTED["months"]["2026-10"]["income"] == 8500000 and EXPECTED["months"]["2026-10"]["net_cash_flow"] == -7196225
    for needle in ("Rs 85,000.00", "Rs 1,56,962.25", "-Rs 71,962.25", "Savings rate", "-84.66%"):
        assert needle in text, needle


def test_two_users_same_statement_have_independent_figures(client, auth, loaded):
    other = register(client, "bob@example.com")
    assert client.get(f"{API}/transactions", headers=other).json()["total"] == 0
    assert dash(client, other, "2026-10")["totals"]["income"] == 0
    _, res = (lambda p: (p, client.post(f"{API}/imports/{p['import_id']}/commit", headers=other, json={}).json()))(upload(client, other, STATEMENT.read_bytes()).json())
    assert res["inserted"] == 95
    assert dash(client, auth, "2026-10")["totals"]["net_expense"] == EXPECTED["months"]["2026-10"]["net_expense"] == dash(client, other, "2026-10")["totals"]["net_expense"]
    tx = client.get(f"{API}/transactions", headers=auth, params={"page_size": 1}).json()["items"][0]
    client.delete(f"{API}/transactions/{tx['id']}", headers=other)  # a different id of bob's: must not touch alice
    assert client.get(f"{API}/transactions/{tx['id']}", headers=auth).status_code == 200
