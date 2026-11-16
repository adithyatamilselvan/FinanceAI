"""Small shared helpers: ids, time, serialization, description normalisation."""
import re
from datetime import date, datetime, timezone
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import HTTPException


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_oid(value: str, what: str = "id") -> ObjectId:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail=f"{what} not found")


def day_start(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def as_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).date() if v.tzinfo else v.date()
    return v


def month_key(d: date | datetime) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def month_bounds(period: str) -> tuple[datetime, datetime]:
    """[start, end) UTC datetimes for 'YYYY-MM'. Handles December rollover and any month length."""
    y, m = int(period[:4]), int(period[5:7])
    start = datetime(y, m, 1, tzinfo=timezone.utc)
    end = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=timezone.utc)
    return start, end


def add_months(period: str, n: int) -> str:
    y, m = int(period[:4]), int(period[5:7])
    idx = y * 12 + (m - 1) + n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def months_between(a: str, b: str) -> int:
    """Number of months from period a to period b (b - a)."""
    return (int(b[:4]) * 12 + int(b[5:7])) - (int(a[:4]) * 12 + int(a[5:7]))


PERIOD_RE = r"^\d{4}-(0[1-9]|1[0-2])$"

_NOISE_TOKENS = {
    "upi", "neft", "imps", "rtgs", "pos", "ach", "ecs", "nach", "txn", "ref", "no", "payment", "pay", "to", "from",
    "by", "the", "and", "in", "of", "on", "debit", "credit", "card", "online", "bank", "ltd", "pvt", "limited",
    "mob", "mobile", "bill", "dr", "cr", "transfer", "trf", "ibl", "ybl", "okaxis", "oksbi", "okhdfcbank", "paytm",
    "okicici", "axl", "apl",
}


def normalize_description(desc: str) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (desc or "").lower())
    return re.sub(r"\s+", " ", s).strip()


def merchant_key(desc: str) -> str:
    """Stable key identifying the counterparty: first 3 meaningful, digit-free tokens."""
    toks = [t for t in normalize_description(desc).split() if not any(c.isdigit() for c in t) and t not in _NOISE_TOKENS and len(t) > 1]
    return " ".join(toks[:3])


def clean_text(s: str | None, max_len: int = 200) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", s or "")
    return re.sub(r"\s+", " ", s).strip()[:max_len]


def process_rss_mb() -> float | None:
    """Resident memory of this process in MB (development diagnostic). psutil if present, else /proc."""
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / 1048576, 1)
    except ImportError:
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return round(int(line.split()[1]) / 1024, 1)
        except OSError:
            pass
    return None
