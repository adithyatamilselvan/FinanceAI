"""Bank statement parsing (CSV/XLSX), column mapping, normalisation, validation and duplicate detection.

Uploaded bytes are parsed in memory and never written to disk. Pandas is imported lazily so the
application starts without paying its import cost.
"""
import csv
import hashlib
import io
import re
from datetime import datetime, timezone

from ..money import SUPPORTED_CURRENCIES, parse_amount_to_minor
from ..util import clean_text, normalize_description
from . import categorizer as cz

HEADER_SYNONYMS = {
    "date": ["date", "txn date", "transaction date", "value date", "posting date", "tran date", "booking date", "trans date"],
    "description": ["description", "narration", "particulars", "details", "transaction details", "remarks", "transaction remarks", "memo", "payee"],
    "amount": ["amount", "transaction amount", "amt", "value"],
    "debit": ["debit", "withdrawal", "withdrawals", "withdrawal amt", "debit amount", "dr", "paid out", "money out"],
    "credit": ["credit", "deposit", "deposits", "deposit amt", "credit amount", "cr", "paid in", "money in"],
    "type": ["type", "dr/cr", "cr/dr", "transaction type", "debit/credit"],
    "balance": ["balance", "closing balance", "running balance"],
}


MAX_XLSX_UNCOMPRESSED = 40 * 1024 * 1024  # a 5,000-row statement unpacks to well under 2 MB


class ImportError_(ValueError):
    """User-facing import problem (bad file, too large, unreadable)."""


def _norm_header(h: str) -> str:
    """Lower-case, punctuation-insensitive: 'Withdrawal Amt.' -> 'withdrawal amt', 'Dr/Cr' -> 'dr cr'."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(h or "").lower()).split())


HEADER_SYNONYMS["description"] += ["transaction remarks", "narrative", "transaction description", "description of transaction", "remarks"]
HEADER_SYNONYMS["debit"] += ["withdrawal amount", "withdrawal amount inr", "debit amount inr", "dr amount", "debits"]
HEADER_SYNONYMS["credit"] += ["deposit amount", "deposit amount inr", "credit amount inr", "cr amount", "credits"]
HEADER_SYNONYMS["date"] += ["txn dt", "transaction dt"]
HEADER_SYNONYMS["type"] += ["dr cr", "cr dr", "debit credit"]
HEADER_SYNONYMS = {k: list(dict.fromkeys(_norm_header(x) for x in v)) for k, v in HEADER_SYNONYMS.items()}


def read_rows(filename: str, data: bytes, max_rows: int) -> list[list[str]]:
    """Return raw rows (list of string cells) from a CSV or XLSX upload."""
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        if not data.startswith(b"PK"):
            raise ImportError_("The file is not a valid .xlsx workbook")
        return _read_xlsx(data, max_rows)
    if name.endswith(".csv"):
        if data.startswith(b"PK") or b"\x00" in data[:2048]:
            raise ImportError_("The file does not look like a text CSV")
        return _read_csv(data, max_rows)
    raise ImportError_("Only .csv and .xlsx files are supported")


def _read_csv(data: bytes, max_rows: int) -> list[list[str]]:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ImportError_("Could not decode the CSV (expected UTF-8 or Windows-1252)")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows: list[list[str]] = []
    for row in csv.reader(io.StringIO(text), dialect):
        if any(c.strip() for c in row):
            rows.append([c.strip() for c in row])
        if len(rows) > max_rows + 60:
            raise ImportError_(f"File has more than {max_rows} transaction rows; split it into smaller files")
    return rows


def _read_xlsx(data: bytes, max_rows: int) -> list[list[str]]:
    import zipfile

    from openpyxl import load_workbook

    # An .xlsx is a zip: a few KB can expand to gigabytes (zip bomb). Check declared sizes BEFORE parsing anything.
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
            if len(infos) > 200 or sum(i.file_size for i in infos) > MAX_XLSX_UNCOMPRESSED or any(i.file_size > MAX_XLSX_UNCOMPRESSED // 2 for i in infos):
                raise ImportError_("The workbook is too large when unpacked. Export a shorter date range or save it as CSV.")
    except zipfile.BadZipFile as e:
        raise ImportError_("The file is not a valid .xlsx workbook") from e
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # openpyxl raises many types for corrupt files
        raise ImportError_("Could not read the .xlsx workbook") from e
    try:
        ws = wb.worksheets[0]
        rows: list[list[str]] = []
        for r in ws.iter_rows(values_only=True):
            cells = []
            for v in r:
                if v is None:
                    cells.append("")
                elif isinstance(v, datetime):
                    cells.append(v.date().isoformat())
                elif isinstance(v, float):
                    cells.append(repr(v))
                else:
                    cells.append(str(v).strip())
            if any(cells):
                rows.append(cells)
            if len(rows) > max_rows + 60:
                raise ImportError_(f"File has more than {max_rows} transaction rows; split it into smaller files")
        return rows
    finally:
        wb.close()


def detect_header(rows: list[list[str]]) -> tuple[int, list[str], bool]:
    """Find the header row (statements often start with bank/account preamble rows).

    Returns (index, headers, recognised). When no row has two recognisable column names the widest of the
    first rows is used and recognised=False, so the user can map the columns manually.
    """
    best, best_score = -1, 0
    all_syn = {s for v in HEADER_SYNONYMS.values() for s in v}
    for i, row in enumerate(rows[:40]):
        score = sum(1 for c in row if _norm_header(c) in all_syn)
        if score > best_score:
            best, best_score = i, score
    if best_score >= 2:
        return best, rows[best], True
    widest = max(range(min(len(rows), 10)), key=lambda i: len([c for c in rows[i] if c.strip()]))
    if len([c for c in rows[widest] if c.strip()]) < 2 or widest + 1 >= len(rows):
        raise ImportError_("Could not find a header row followed by transaction rows")
    return widest, rows[widest], False


def suggest_mapping(headers: list[str]) -> dict:
    norm = {_norm_header(h): h for h in headers if h}
    out: dict = {}
    for field, syns in HEADER_SYNONYMS.items():
        for s in syns:
            if s in norm:
                out[field] = norm[s]
                break
    if "amount" in out and ("debit" in out or "credit" in out):
        out.pop("amount")  # separate debit/credit columns take precedence
    out.pop("balance", None)
    return out


def _fingerprint(date_iso: str, amount: int, norm_desc: str, kind: str, occurrence: int) -> str:
    raw = f"{date_iso}|{amount}|{norm_desc}|{kind}|{occurrence}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def normalize(rows: list[list[str]], header_idx: int, mapping: dict, currency: str, dayfirst: bool, max_rows: int) -> dict:
    """Normalise rows to transaction candidates. Returns {'rows': [...], 'errors': [...]}.

    Each good row: {row, date(iso), description, amount(int>0), type, category, fingerprint, category_source}
    Invalid rows are reported in errors with a reason; they are never silently imported.
    """
    import pandas as pd

    if currency not in SUPPORTED_CURRENCIES:
        raise ImportError_("Unsupported currency")
    headers = rows[header_idx]
    idx = {h: i for i, h in enumerate(headers)}
    for key in ("date", "description"):
        if mapping.get(key) not in idx:
            raise ImportError_(f"Mapped column for '{key}' was not found in the file")
    has_amount = mapping.get("amount") in idx
    has_split = mapping.get("debit") in idx or mapping.get("credit") in idx
    if not has_amount and not has_split:
        raise ImportError_("Map either an amount column or debit/credit columns")
    body = rows[header_idx + 1:]
    if len(body) > max_rows:
        raise ImportError_(f"File has {len(body)} rows; the limit is {max_rows}")
    if not body:
        raise ImportError_("The file contains no transaction rows")

    def cell(r: list[str], col: str | None) -> str:
        if col is None or col not in idx:
            return ""
        i = idx[col]
        return r[i] if i < len(r) else ""

    date_raw = pd.Series([cell(r, mapping["date"]) for r in body], dtype="string")
    parsed = pd.to_datetime(date_raw, errors="coerce", dayfirst=dayfirst, format="mixed", utc=False)
    # ISO strings must never be re-interpreted day-first.
    iso = date_raw.str.match(r"^\d{4}-\d{2}-\d{2}")
    if iso.any():
        parsed[iso.fillna(False)] = pd.to_datetime(date_raw[iso.fillna(False)].str[:10], errors="coerce", format="%Y-%m-%d")

    good: list[dict] = []
    errors: list[dict] = []
    seen: dict[tuple, int] = {}
    for n, r in enumerate(body):
        line = header_idx + 2 + n  # 1-based spreadsheet line
        ts = parsed.iloc[n]
        if pd.isna(ts):
            errors.append({"row": line, "reason": f"Invalid or missing date '{date_raw.iloc[n]}'"})
            continue
        d = ts.date()
        if d.year < 1990 or (d - datetime.now(timezone.utc).date()).days > 366:
            errors.append({"row": line, "reason": f"Date {d.isoformat()} is out of the accepted range"})
            continue
        desc = clean_text(cell(r, mapping["description"]))
        if not desc:
            errors.append({"row": line, "reason": "Missing description"})
            continue
        try:
            signed = _signed_amount(r, cell, mapping, has_amount)
        except ValueError as e:
            errors.append({"row": line, "reason": str(e)})
            continue
        if signed == 0:
            errors.append({"row": line, "reason": "Zero amount"})
            continue
        kind = "income" if signed > 0 else "expense"
        if cz.is_transfer_text(desc):
            kind = "transfer"
        elif kind == "income" and cz.is_refund_text(desc):
            kind = "refund"
        amount = abs(signed)
        norm = normalize_description(desc)
        key = (d.isoformat(), amount, norm, kind)
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        good.append({"row": line, "date": d.isoformat(), "description": desc, "amount": amount, "type": kind, "flow": "in" if signed > 0 else "out",
                     "fingerprint": _fingerprint(d.isoformat(), amount, norm, kind, occ), "currency": currency})
    return {"rows": good, "errors": errors}


def _signed_amount(r, cell, mapping, has_amount) -> int:
    if has_amount:
        raw = cell(r, mapping.get("amount"))
        val = parse_amount_to_minor(raw)
        tcol = cell(r, mapping.get("type")).strip().lower()
        if tcol in ("dr", "debit", "d", "withdrawal"):
            val = -abs(val)
        elif tcol in ("cr", "credit", "c", "deposit"):
            val = abs(val)
        return val
    deb_raw, cred_raw = cell(r, mapping.get("debit")), cell(r, mapping.get("credit"))
    deb = parse_amount_to_minor(deb_raw) if deb_raw.strip() not in ("", "-") else 0
    cred = parse_amount_to_minor(cred_raw) if cred_raw.strip() not in ("", "-") else 0
    if deb and cred:
        raise ValueError("Both debit and credit are filled")
    if not deb and not cred:
        raise ValueError("Neither debit nor credit has an amount")
    return cred - abs(deb) if cred else -abs(deb)


def pair_internal_transfers(rows: list[dict]) -> int:
    """Pair a debit and a credit of identical amount within 1 day as a transfer between the user's own accounts.

    Deliberately conservative to avoid hiding real income/expenses: BOTH legs must be explicitly transfer-like
    (type already 'transfer' from wording such as "self transfer", or wording containing 'transfer'/'self'/'own a/c').
    A bare channel word such as NEFT/IMPS is NOT enough, so rent 22,000 and a friend's NEFT credit of 22,000 on the
    same day stay income and expense.
    """
    from datetime import date as _d

    def explicit(r):
        d = r["description"].lower()
        return r["type"] == "transfer" or "transfer" in d or " self" in f" {d}" or "own a/c" in d or "own acc" in d

    outs = [r for r in rows if r["flow"] == "out" and r["type"] in ("expense", "transfer") and explicit(r)]
    ins = [r for r in rows if r["flow"] == "in" and r["type"] in ("income", "transfer") and explicit(r)]
    used: set[int] = set()
    n = 0
    for o in outs:
        do = _d.fromisoformat(o["date"])
        for i, c in enumerate(ins):
            if i not in used and c["amount"] == o["amount"] and abs((_d.fromisoformat(c["date"]) - do).days) <= 1:
                o["type"] = c["type"] = "transfer"
                used.add(i)
                n += 2
                break
    return n


def categorize_rows(rows: list[dict], user_rules: dict[str, str]) -> None:
    for r in rows:
        r["category"], r["category_source"] = cz.categorize(r["description"], r["type"], user_rules)
