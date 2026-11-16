import pytest

from app.money import fmt, mul_div_round, parse_amount_to_minor
from app.services import simulation as sim
from app.services.categorizer import categorize, is_refund_text, is_transfer_text
from app.util import add_months, merchant_key, month_bounds


@pytest.mark.parametrize("raw,expected", [
    ("1,234.50", 123450), ("(500.00)", -50000), ("-250", -25000), ("Rs. 2,00,000.25", 20000025), ("100.5 Cr", 10050),
    ("100.5 Dr", -10050), ("0.005", 1), ("0.004", 0), (12.34, 1234), (5, 500),
])
def test_parse_amount(raw, expected):
    assert parse_amount_to_minor(raw) == expected


@pytest.mark.parametrize("bad", ["", "abc", None, "nan", "--", True, float("inf"), "9" * 30])
def test_parse_amount_rejects(bad):
    with pytest.raises(ValueError):
        parse_amount_to_minor(bad)


def test_no_float_drift_in_sum():
    total = sum(parse_amount_to_minor("0.10") for _ in range(10))
    assert total == 100  # 10 x 0.10 == 1.00 exactly (floats would give 0.9999999999999999)


def test_mul_div_round_half_up():
    assert mul_div_round(1, 1, 2) == 1 and mul_div_round(-1, 1, 2) == -1 and mul_div_round(10, 1, 3) == 3 and mul_div_round(100, 52, 12) == 433


def test_fmt_indian_grouping():
    assert fmt(12345678900, "INR") == "Rs 12,34,56,789.00" and fmt(-5, "USD") == "-$0.05"


def test_month_helpers_cover_december_and_leap():
    s, e = month_bounds("2024-12")
    assert (e.year, e.month, e.day) == (2025, 1, 1)
    s, e = month_bounds("2024-02")
    assert (e - s).days == 29
    s, e = month_bounds("2025-02")
    assert (e - s).days == 28
    assert add_months("2025-01", -1) == "2024-12" and add_months("2024-11", 14) == "2026-01"


def test_merchant_key_ignores_refs_and_noise():
    assert merchant_key("UPI/402311119999/SWIGGY/okaxis") == "swiggy"
    assert merchant_key("NETFLIX.COM 123456 MUMBAI") == "netflix com mumbai"


def test_categorizer_defaults():
    cases = {"Salary ACME Corp": ("income", "Salary"), "House Rent Landlord": ("expense", "Housing"), "Netflix Subscription": ("expense", "Subscriptions"),
             "BigBasket": ("expense", "Groceries"), "Swiggy Order": ("expense", "Food"), "Uber Ride": ("expense", "Transport"),
             "Apollo Pharmacy": ("expense", "Healthcare"), "Amazon Pay": ("expense", "Shopping"), "HDFC Home Loan EMI": ("expense", "Loan Repayment"),
             "Random Unknown Shop xyz": ("expense", "Other"), "Interest credited": ("income", "Interest"), "Mystery deposit": ("income", "Other Income"),
             "Amazon Prime membership": ("expense", "Subscriptions")}
    for desc, (kind, cat) in cases.items():
        assert categorize(desc, kind)[0] == cat, desc
    assert categorize("anything", "transfer")[0] == "Transfer"
    assert categorize("Corner Cafe Visit", "expense", {"corner cafe visit": "Entertainment"})[0] == "Entertainment"


def test_transfer_and_refund_hints():
    assert is_transfer_text("Self Transfer to own account") and not is_transfer_text("Swiggy")
    assert is_refund_text("Refund - Amazon order") and is_refund_text("UPI reversal 123") and not is_refund_text("Salary")


def test_emi_known_value():
    assert sim.emi(100000000, 10, 120) == 1321507  # Rs 13,215.07 for Rs 10,00,000 @ 10% over 10 years
    assert sim.emi(120000, 0, 12) == 10000


def test_amortization_reconciles_exactly():
    a = sim.amortize(100000000, 10, 120)
    assert a["months"] == 120 and a["schedule"][-1]["balance"] == 0
    assert sum(r["principal"] for r in a["schedule"]) == 100000000
    assert sum(r["interest"] for r in a["schedule"]) == a["total_interest"]
    assert abs(a["total_paid"] - 1321507 * 120) < 200  # last instalment absorbs rounding
    assert a["schedule"][0]["interest"] == 833333  # Rs 10,00,000 * 10%/12 = Rs 8,333.33


def test_extra_payment_saves_time_and_interest():
    out = sim.loan_analysis(100000000, 10, 120, 500000)
    e = out["with_extra_payment"]
    assert e["months_saved"] > 0 and e["interest_saved"] > 0 and e["months"] < 120


def test_accumulate_with_and_without_return():
    assert sim.accumulate(0, [100] * 3, 0) == [100, 200, 300]
    s = sim.accumulate(10000, [0] * 12, 12)  # 1%/month on 100.00 for a year
    assert s[-1] == 11267  # exact 11268.25; rounding half-up to the minor unit every month gives 11267


def test_invalid_tenure_rejected():
    with pytest.raises(ValueError):
        sim.emi(100000, 10, 0)
