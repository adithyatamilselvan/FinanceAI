"""Generate SYNTHETIC bank-statement test data (fictional person, fictional merchants/amounts, no real accounts).

Outputs (next to this script):
  sample_bank_statement.csv        HDFC-style statement, Aug-Oct 2026, what a user would upload
  sample_bank_statement.truth.csv  ground truth per row: what each row SHOULD be classified as
  expected_figures.json            aggregates computed INDEPENDENTLY of the application code (Decimal arithmetic,
                                   straight from the ground truth) - used by pytest and Playwright to verify the app

Run:  python generate_synthetic_data.py
"""
import csv
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

OUT = Path(__file__).parent
MONTHS = [(2026, 8, "AUG"), (2026, 9, "SEP"), (2026, 10, "OCT")]

rows: list[dict] = []  # date, narration, amount(Decimal rupees), flow D|C, type, category


def add(m: int, day: int, narration: str, amount: str, flow: str, typ: str, cat: str):
    rows.append({"date": date(2026, m, day), "narration": narration, "amount": D(amount), "flow": flow, "type": typ, "category": cat})


def exp(m, d, n, a, cat):
    add(m, d, n, a, "D", "expense", cat)


for k, (y, m, mon) in enumerate(MONTHS):
    # ---- income ----
    add(m, 1, f"NEFT CR-ACME TECHNOLOGIES PVT LTD-SALARY {mon} 2026", "85000.00", "C", "income", "Salary")
    # ---- fixed commitments ----
    exp(m, 3, f"NEFT DR-LANDLORD R KUMAR-RENT {mon}", "22000.00", "Housing")
    exp(m, 5, "ACH D- HDFC BANK LOAN EMI 4471", "6500.00", "Loan Repayment")
    exp(m, 7, "ACH D- HDFC ERGO HEALTH INSURANCE PREMIUM", "1850.00", "Insurance")
    exp(m, 8, "ACH D- ACT FIBERNET BROADBAND", "999.00", "Utilities")
    exp(m, 10, "BESCOM ELECTRICITY BILL PAYMENT", ["1840.00", "1615.00", "2210.00"][k], "Utilities")
    exp(m, 12, "ACH D- NETFLIX ENTERTAINMENT SUBSCRIPTION", "649.00", "Subscriptions")
    exp(m, 15, "UPI-SPOTIFY INDIA-spotify@ybl", "119.00", "Subscriptions")
    exp(m, 16, "UPI-JIO PREPAID RECHARGE", "299.00", "Utilities")
    # ---- groceries ----
    g = [["2340.50", "1876.00", "645.25", "3120.75"], ["2512.00", "1990.50", "712.00", "2845.25"], ["2688.75", "2105.00", "598.50", "3305.00"]][k]
    for day, name, amt in zip((4, 11, 19, 27), ("UPI-BIGBASKET-bigbasket@icici", "POS 4521 DMART AVENUE SUPERMARKET", "UPI-BLINKIT-blinkit@ybl", "POS 8812 RELIANCE FRESH GROCERY"), g, strict=True):
        exp(m, day, name, amt, "Groceries")
    # ---- transport ----
    t = [["245.00", "2000.00", "312.00", "500.00"], ["268.00", "2100.00", "295.00", "500.00"], ["251.00", "1950.00", "330.00", "500.00"]][k]
    for day, name, amt in zip((2, 9, 17, 24), ("UPI-UBER INDIA-uber@axl", "POS INDIAN OIL PETROL PUMP", "UPI-OLA CABS-ola@icici", "UPI-NAMMA METRO CARD TOPUP"), t, strict=True):
        exp(m, day + (1 if k == 2 and day == 24 else 0), name, amt, "Transport")
    # ---- cash ----
    exp(m, 15, f"ATM WDL 1123 KORAMANGALA {mon}", ["5000.00", "3000.00", "4000.00"][k], "Other")
    # ---- transfers (must not count as income or spending) ----
    add(m, 2, "NEFT DR-SELF TRANSFER TO SAVINGS A/C XX4471", "15000.00", "D", "transfer", "Transfer")
    add(m, 25, "CREDIT CARD PAYMENT HDFC XX9921", ["14200.00", "11850.00", "16300.00"][k], "D", "transfer", "Transfer")

# ---- food (incl. unusual Oct party order and a duplicate-looking pair) ----
exp(8, 6, "UPI-SWIGGY-swiggy@icici ORDER 1", "428.00", "Food")
exp(8, 13, "UPI-ZOMATO-zomato@hdfcbank ORDER", "612.50", "Food")
exp(8, 21, "POS 1123 CAFE COFFEE DAY", "310.00", "Food")
exp(8, 28, "UPI-SWIGGY-swiggy@icici ORDER 2", "389.00", "Food")
exp(9, 5, "UPI-ZOMATO-zomato@hdfcbank ORDER", "540.00", "Food")
exp(9, 12, "UPI-SWIGGY-swiggy@icici ORDER 3", "455.50", "Food")
exp(9, 22, "UPI-ROYAL RESTAURANT-royalrest@ybl", "1260.00", "Food")
exp(9, 29, "UPI-SWIGGY-swiggy@icici ORDER 4", "372.00", "Food")
exp(10, 4, "UPI-SWIGGY-swiggy@icici ORDER 5", "498.00", "Food")
exp(10, 11, "UPI-ZOMATO-zomato@hdfcbank ORDER", "623.00", "Food")
exp(10, 18, "UPI-SWIGGY-swiggy@icici ORDER 6", "486.00", "Food")
exp(10, 18, "UPI-SWIGGY-swiggy@icici ORDER 6", "486.00", "Food")  # identical twin: possible duplicate charge
exp(10, 25, "UPI-ZOMATO BULK PARTY ORDER-zomato@hdfcbank", "6800.00", "Food")  # unusual
# ---- healthcare (Oct hospital bill = category spike) ----
exp(8, 14, "POS 3321 APOLLO PHARMACY", "842.00", "Healthcare")
exp(8, 25, "UPI-DR SHAH CLINIC CONSULT", "600.00", "Healthcare")
exp(9, 20, "UPI-MEDPLUS PHARMACY-medplus@ybl", "615.00", "Healthcare")
exp(10, 8, "POS 3321 APOLLO PHARMACY", "720.00", "Healthcare")
exp(10, 21, "NEFT DR-CITY HOSPITAL BILLING", "9800.00", "Healthcare")
# ---- entertainment ----
exp(8, 23, "UPI-BOOKMYSHOW-bms@icici", "760.00", "Entertainment")
exp(9, 26, "POS 7710 PVR CINEMAS", "840.00", "Entertainment")
exp(10, 16, "UPI-BOOKMYSHOW-bms@icici", "690.00", "Entertainment")
# ---- shopping (Sep refund of the Aug Amazon order, Oct big purchase = unusual) ----
exp(8, 9, "UPI-AMAZON PAY-amazon@apl ORDER 40231", "1299.00", "Shopping")
exp(8, 18, "POS MYNTRA DESIGNS", "2150.00", "Shopping")
exp(9, 8, "UPI-AMAZON PAY-amazon@apl ORDER 51877", "899.00", "Shopping")
exp(9, 16, "POS MYNTRA DESIGNS", "1740.00", "Shopping")
add(9, 24, "REFUND-AMAZON ORDER 40231", "1299.00", "C", "refund", "Shopping")
exp(10, 7, "UPI-AMAZON PAY-amazon@apl ORDER 66120", "1120.00", "Shopping")
exp(10, 22, "POS MYNTRA DESIGNS", "1985.00", "Shopping")
exp(10, 29, "POS 9001 APPLE STORE IPAD PRO", "64900.00", "Shopping")  # unusual
# ---- travel ----
exp(10, 14, "NEFT DR-MAKEMYTRIP FLIGHT BLR-DEL", "18500.00", "Travel")
# ---- other income ----
add(9, 14, "UPI-CLIENT PAYMENT-INVOICE 204-DESIGNWORKS", "12000.00", "C", "income", "Business Income")
add(9, 30, "INT.PD 01-07-26 TO 30-09-26", "412.00", "C", "income", "Interest")
# ---- transfer pair (both legs explicit) and a TRAP: unrelated NEFT credit equal to the rent, same day ----
add(9, 18, "IMPS-SELF TRANSFER TO OWN A/C WALLET", "5000.00", "D", "transfer", "Transfer")
add(9, 18, "IMPS-SELF TRANSFER FROM OWN A/C WALLET", "5000.00", "C", "transfer", "Transfer")
add(9, 3, "NEFT CR-FRIEND AMIT-REPAYMENT OF LOAN", "22000.00", "C", "income", "Other Income")  # must NOT pair with rent

rows.sort(key=lambda r: r["date"])  # stable: keeps same-day insertion order


def indian(a: D) -> str:
    s = f"{a:.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{whole}.{frac}"


bal = D("120000.00")
with open(OUT / "sample_bank_statement.csv", "w", newline="", encoding="utf-8") as f, open(OUT / "sample_bank_statement.truth.csv", "w", newline="", encoding="utf-8") as t:
    w, tw = csv.writer(f), csv.writer(t)
    w.writerow(["Date", "Narration", "Chq./Ref.No.", "Value Dt", "Withdrawal Amt.", "Deposit Amt.", "Closing Balance"])
    tw.writerow(["line", "date", "narration", "amount_paise", "expected_type", "expected_category"])
    for i, r in enumerate(rows):
        bal += r["amount"] if r["flow"] == "C" else -r["amount"]
        ds = r["date"].strftime("%d/%m/%y")
        ref = f"{(abs(hash(r['narration'])) % 10**12 if False else (i * 7919 + 100003) % 10**12):012d}"
        w.writerow([ds, r["narration"], ref, ds, indian(r["amount"]) if r["flow"] == "D" else "", indian(r["amount"]) if r["flow"] == "C" else "", indian(bal)])
        tw.writerow([i + 2, r["date"].isoformat(), r["narration"], int(r["amount"] * 100), r["type"], r["category"]])
    r_final = bal

# ---------------- independent aggregates (pure Decimal, straight from ground truth) ----------------
FIXED = {"Housing", "Utilities", "Insurance", "Loan Repayment", "Subscriptions"}
ESSENTIAL = {"Housing", "Groceries", "Healthcare", "Utilities", "Insurance", "Loan Repayment", "Education"}
paise = lambda a: int(a * 100)  # noqa: E731
figs: dict = {"synthetic": True, "currency": "INR", "closing_balance_paise": paise(r_final), "months": {}}
for y, m, _ in MONTHS:
    p = f"{y}-{m:02d}"
    inc = gross = ref = D(0)
    cats: dict[str, D] = {}
    inc_cats: dict[str, D] = {}
    n = 0
    for r in rows:
        if r["date"].month != m:
            continue
        n += 1
        if r["type"] == "income":
            inc += r["amount"]
            inc_cats[r["category"]] = inc_cats.get(r["category"], D(0)) + r["amount"]
        elif r["type"] == "expense":
            gross += r["amount"]
            cats[r["category"]] = cats.get(r["category"], D(0)) + r["amount"]
        elif r["type"] == "refund":
            ref += r["amount"]
            cats[r["category"]] = cats.get(r["category"], D(0)) - r["amount"]
    net = gross - ref
    fixed = sum((v for c, v in cats.items() if c in FIXED), D(0))
    figs["months"][p] = {
        "income": paise(inc), "gross_expense": paise(gross), "refunds": paise(ref), "net_expense": paise(net), "net_cash_flow": paise(inc - net),
        "savings_pct": round(float((inc - net) * 100 / inc), 2), "categories": {c: paise(v) for c, v in sorted(cats.items(), key=lambda kv: -kv[1])},
        "income_categories": {c: paise(v) for c, v in inc_cats.items()}, "fixed": paise(fixed), "variable": paise(net - fixed), "rows": n,
        "essential": paise(sum((v for c, v in cats.items() if c in ESSENTIAL), D(0))),
    }
figs["row_count"] = len(rows)
figs["type_counts"] = {t: sum(1 for r in rows if r["type"] == t) for t in ("income", "expense", "refund", "transfer")}
(OUT / "expected_figures.json").write_text(json.dumps(figs, indent=2), encoding="utf-8")
print(f"wrote {len(rows)} rows; closing balance {r_final}")
