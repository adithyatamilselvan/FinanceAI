"""Integer minor-unit money helpers. No binary floats are used for stored amounts.

All amounts are integers in minor units (paise for INR, cents for USD...).
"""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

SUPPORTED_CURRENCIES = {"INR": 2, "USD": 2, "EUR": 2, "GBP": 2}
MAX_AMOUNT_MINOR = 10**13  # 100 billion major units: sanity cap

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?|\.\d+")


def parse_amount_to_minor(raw: object, decimals: int = 2) -> int:
    """Parse a bank-statement style amount into signed integer minor units.

    Accepts '1,234.50', '(500.00)', '-250', '₹ 1,00,000.25', '100.5 Cr' (Cr positive, Dr negative).
    Raises ValueError for anything unparseable. Rounds half-up at the minor unit.
    """
    if raw is None:
        raise ValueError("empty amount")
    if isinstance(raw, bool):
        raise ValueError("invalid amount")
    if isinstance(raw, int):
        d = Decimal(raw)
    elif isinstance(raw, float):
        if raw != raw or raw in (float("inf"), float("-inf")):
            raise ValueError("invalid amount")
        d = Decimal(repr(raw))
    else:
        s = str(raw).strip()
        if not s or s.lower() in {"nan", "none", "null", "-"}:
            raise ValueError("empty amount")
        neg = bool(re.search(r"(?i)dr\.?$", s))
        s = re.sub(r"(?i)(dr|cr)\.?$", "", s).strip()
        m = _NUM_RE.search(s)
        prefix, rest = (s[: m.start()], s[m.end():]) if m else ("", "")
        if not m or any(ch.isdigit() for ch in prefix + rest):
            raise ValueError("invalid amount")
        if "-" in prefix or ("(" in prefix and ")" in rest):
            neg = True
        try:
            d = Decimal(m.group().replace(",", ""))
        except InvalidOperation as e:
            raise ValueError("invalid amount") from e
        if neg:
            d = -d
    q = Decimal(1).scaleb(-decimals)
    minor = int((d.quantize(q, rounding=ROUND_HALF_UP) * (10**decimals)).to_integral_value())
    if abs(minor) > MAX_AMOUNT_MINOR:
        raise ValueError("amount out of range")
    return minor


def mul_div_round(value: int, numerator: int, denominator: int) -> int:
    """round_half_up(value * numerator / denominator) in pure integer arithmetic (non-negative denominators)."""
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    n = value * numerator
    sign = -1 if n < 0 else 1
    n = abs(n)
    return sign * ((2 * n + denominator) // (2 * denominator))


def pct(part: int, whole: int) -> float | None:
    """Percentage for display only (never used to feed balances). None if whole is 0."""
    if whole == 0:
        return None
    return round(part * 100 / whole, 2)


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def fmt(minor: int, currency: str = "INR") -> str:
    """Human-readable amount for messages and reports (Indian digit grouping for INR)."""
    sign = "-" if minor < 0 else ""
    major, frac = divmod(abs(minor), 100)
    s = str(major)
    if currency == "INR" and len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    else:
        s = f"{major:,}"
    sym = {"INR": "Rs ", "USD": "$", "EUR": "EUR ", "GBP": "GBP "}.get(currency, "")
    return f"{sign}{sym}{s}.{frac:02d}"
