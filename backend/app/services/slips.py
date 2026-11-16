"""Payment-slip batches: processing one file, review flags, duplicate detection, commit rules and the Excel workbook.

Design:
  * A batch is a MongoDB document (`slip_batches`) owned by one user and deleted automatically after `slip_batch_ttl_hours`
    (TTL index + explicit cleanup). Uploaded files are processed in memory and never written to disk or stored.
  * Nothing reaches `transactions` until the user confirms; confirmed slips are saved with the same document shape as every other
    transaction, so dashboards, budgets, forecasts and reports use the one existing financial engine.
"""
import hashlib
import io
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from ..config import get_settings
from ..util import clean_text, day_start, merchant_key, normalize_description, now_utc
from . import categorizer as cz
from . import ocr
from .slip_parser import parse_slip

REQUIRED = ("date", "amount", "type", "currency")
OPTIONAL_ABSENT_CHECK = ("time", "reference", "provider", "payment_method")
FIELD_LABELS = {"date": "date", "amount": "amount", "type": "transaction type", "currency": "currency", "merchant": "merchant/recipient"}
STATUS_LABELS = {
    "ready": "Ready to import", "needs_review": "Needs review", "missing_details": "Missing details", "possible_duplicate": "Possible duplicate",
    "already_imported": "Already imported", "payment_failed": "Payment failed (not importable)", "not_successful": "Needs your decision (not a successful payment)",
    "failed": "Could not extract", "imported": "Imported", "skipped": "Skipped",
}


# ------------------------------------------------------------------ one file
def safe_filename(name: str | None) -> str:
    base = re.split(r"[\\/]", name or "upload")[-1]
    return clean_text(base, 100) or "upload"


def _blank_fields() -> dict:
    return {k: None for k in ("date", "time", "amount", "currency", "type", "merchant", "sender", "payment_method", "reference", "provider", "status", "description")}


def process_file(filename: str, content_type: str | None, data: bytes, today: date | None = None) -> dict:
    """OCR + parse one uploaded file. Never raises for bad input: returns an item with ok=False and a user-safe error."""
    s = get_settings()
    name = safe_filename(filename)
    item = {"id": uuid.uuid4().hex, "file": name, "ok": False, "error": None, "method": None, "pages": None, "ocr_confidence": None, "fields": _blank_fields(),
            "category": None, "uncertain": {}, "notes": {}, "verified": False, "edited": [], "raw_text": "", "transaction_id": None, "skipped": False}
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    try:
        if ext not in ocr.ALLOWED_EXTENSIONS:
            raise ocr.SlipFileError("Only JPG, JPEG, PNG and PDF files are supported.")
        declared = (content_type or "").split(";")[0].strip().lower()
        if declared and declared != "application/octet-stream" and declared not in ocr.ALLOWED_MIME[ext]:
            raise ocr.SlipFileError(f"The file type '{declared}' does not match a .{ext} file.")
        if not data:
            raise ocr.SlipFileError("The file is empty.")
        if len(data) > s.slip_max_file_bytes:
            raise ocr.SlipFileError(f"The file is too large ({len(data) / 1048576:.1f} MB). The limit is {s.slip_max_file_bytes // 1048576} MB.")
        res = ocr.extract_text(ext, data)
    except ocr.SlipFileError as e:
        item["error"] = str(e)
        return item
    except ocr.OcrUnavailable as e:
        item["error"] = str(e)
        return item
    item.update(method=res.method, pages=res.pages, ocr_confidence=None if res.confidence is None else round(res.confidence, 1), raw_text=res.text[:4000])
    if not res.text.strip():
        item["error"] = "No readable text was found. Try a sharper, brighter, upright photo."
        return item
    parsed = parse_slip(res.text, today)
    f = parsed["fields"]
    item["fields"], item["uncertain"], item["notes"] = f, parsed["uncertain"], parsed["notes"]
    if f["amount"] is None and f["date"] is None and f["reference"] is None:
        item["error"] = "No transaction details (amount, date or reference) could be found on this file."
        return item
    item["ok"] = True
    return item


# ------------------------------------------------------------------ evaluation
def fingerprint(f: dict) -> str:
    """Stable identity of a payment. A bank/UPI reference wins; otherwise date+time+amount+type+counterparty."""
    ref = re.sub(r"[^A-Z0-9]", "", (f.get("reference") or "").upper())
    if len(ref) >= 8:
        raw = f"ref|{ref}|{f.get('type')}"
    else:
        who = normalize_description(f.get("merchant") or f.get("sender") or "")
        raw = f"fields|{f.get('date')}|{f.get('time') or ''}|{f.get('amount')}|{f.get('type')}|{who}"
    return "slip:" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def _counterparty(f: dict) -> str:
    return f.get("merchant") or f.get("sender") or ""


def review_reasons(item: dict) -> list[str]:
    s = get_settings()
    f = item["fields"]
    out = [f"missing {FIELD_LABELS[k]}" for k in REQUIRED if f.get(k) is None]
    if not _counterparty(f):
        out.append("no merchant/recipient or sender found")
    out += [f"{FIELD_LABELS.get(k, k)}: {why}" for k, why in item["uncertain"].items()]
    c = item.get("ocr_confidence")
    if c is not None and c < s.slip_low_confidence:
        out.append(f"low OCR confidence ({c:.0f}%)")
    return out


def _tokens(s: str) -> set[str]:
    return {t for t in normalize_description(s).split() if len(t) >= 3}


def _existing_duplicate(db, uid, f: dict) -> dict | None:
    """Compare against the user's saved transactions. Returns {kind, level, detail} or None."""
    if f.get("amount") is None or f.get("date") is None:
        return None
    fp = fingerprint(f)
    hit = db.transactions.find_one({"user_id": uid, "fingerprint": fp}, projection={"description": 1, "date": 1})
    if hit:
        return {"kind": "already_imported", "level": "strong", "detail": f"Already in your transactions ({hit['date'].date().isoformat()}: {hit['description'][:60]})"}
    ref = re.sub(r"[^a-z0-9]", "", (f.get("reference") or "").lower())
    if len(ref) >= 8:
        hit = db.transactions.find_one({"user_id": uid, "normalized_description": {"$regex": re.escape(ref)}}, projection={"description": 1, "date": 1})
        if hit:
            return {"kind": "possible_existing", "level": "strong", "detail": f"This reference appears in an existing transaction ({hit['date'].date().isoformat()}: {hit['description'][:60]})"}
    d = datetime.fromisoformat(f["date"]).replace(tzinfo=timezone.utc)
    cur = db.transactions.find({"user_id": uid, "amount": f["amount"], "type": f.get("type"), "date": {"$gte": d - timedelta(days=1), "$lte": d + timedelta(days=1)}},
                               projection={"description": 1, "date": 1}).limit(5)
    who = _tokens(_counterparty(f))
    best = None
    for t in cur:
        overlap = bool(who & _tokens(t["description"]))
        cand = {"kind": "possible_existing", "level": "probable" if overlap else "weak",
                "detail": f"Same amount within a day of an existing transaction ({t['date'].date().isoformat()}: {t['description'][:60]})"}
        if overlap:
            return cand
        best = best or cand
    return best


def _in_batch_key(f: dict) -> tuple:
    return (f.get("date"), f.get("amount"), f.get("type"), normalize_description(_counterparty(f)))


def evaluate(db, uid, items: list[dict]) -> None:
    """Recompute every derived field (missing, review reasons, duplicates, import_status) for all items in a batch."""
    seen_ref: dict[str, str] = {}
    seen_key: dict[tuple, str] = {}
    for it in items:
        f = it["fields"]
        it["duplicate"] = None
        if not it["ok"]:
            it["missing"], it["absent"], it["review_reasons"], it["review_required"], it["import_status"] = [], [], [], False, "failed"
            continue
        it["missing"] = [k for k in REQUIRED if f.get(k) is None] + ([] if _counterparty(f) else ["merchant"])
        it["absent"] = [k for k in OPTIONAL_ABSENT_CHECK if not f.get(k)]
        it["review_reasons"] = review_reasons(it)
        ref = re.sub(r"[^A-Z0-9]", "", (f.get("reference") or "").upper())
        key = _in_batch_key(f)
        if len(ref) >= 8 and ref in seen_ref:
            it["duplicate"] = {"kind": "in_batch", "level": "strong", "detail": f"Same reference as {seen_ref[ref]} in this batch"}
        elif f.get("amount") is not None and f.get("date") and key in seen_key and _counterparty(f):
            it["duplicate"] = {"kind": "in_batch", "level": "probable", "detail": f"Same date, amount and payee as {seen_key[key]} in this batch"}
        else:
            it["duplicate"] = _existing_duplicate(db, uid, f)
        if len(ref) >= 8:
            seen_ref.setdefault(ref, it["file"])
        if f.get("amount") is not None and f.get("date"):
            seen_key.setdefault(key, it["file"])
        it["review_required"] = bool(it["review_reasons"]) or f.get("status") != "successful" or it["duplicate"] is not None
        it["import_status"] = _status_of(it)


def _status_of(it: dict) -> str:
    if it.get("transaction_id"):
        return "imported"
    if it.get("skipped"):
        return "skipped"
    f, dup = it["fields"], it["duplicate"]
    if dup and dup["kind"] == "already_imported":
        return "already_imported"
    if f.get("status") == "failed":
        return "payment_failed"
    if any(k in it["missing"] for k in REQUIRED):
        return "missing_details"
    if dup:
        return "possible_duplicate"
    if f.get("status") != "successful":
        return "not_successful"
    if it["review_reasons"] and not it["verified"]:
        return "needs_review"
    return "ready"


def blockers(it: dict, allow_non_successful: bool, allow_possible_duplicates: bool) -> list[str]:
    """Why this item may NOT be imported with the user's current choices (empty list = importable)."""
    f, dup, out = it["fields"], it["duplicate"], []
    if not it["ok"]:
        return ["extraction failed for this file"]
    if it.get("transaction_id"):
        return ["already imported from this batch"]
    miss = [FIELD_LABELS[k] for k in REQUIRED if f.get(k) is None]
    if miss:
        out.append("missing " + ", ".join(miss) + " (edit the row to fill it in)")
    if f.get("status") == "failed":
        out.append("the payment failed, so it is never imported")
    if dup and dup["kind"] == "already_imported":
        out.append("this payment is already in your transactions")
    elif dup and not allow_possible_duplicates:
        out.append(f"possible duplicate ({dup['detail']}); tick 'import possible duplicates' to override")
    if f.get("status") in ("pending", "refunded", "unknown") and not allow_non_successful:
        out.append(f"status is '{f['status']}'; confirm that you want to import non-successful payments")
    needs_verify = bool(it["review_reasons"]) or (dup and dup["kind"] != "already_imported") or f.get("status") in ("pending", "refunded", "unknown")
    if needs_verify and not it["verified"] and not miss:
        out.append("please review and mark this row as verified (or edit it)")
    return out


# ------------------------------------------------------------------ saving
def transaction_doc(uid, batch_id, it: dict, user: dict, rules: dict[str, str]) -> dict:
    f = it["fields"]
    name = _counterparty(f)
    desc = clean_text(f.get("description") or name or f"Payment slip {it['file']}")
    if name and name.lower() not in desc.lower():
        desc = clean_text(f"{name} - {desc}")
    kind = f["type"]
    category = it.get("category")
    if kind == "transfer":
        category = "Transfer"
    elif not category:
        category, _ = cz.categorize(f"{name} {desc}", kind if kind in ("income", "refund") else "expense", rules)
    now = now_utc()
    return {
        "user_id": uid, "date": day_start(date.fromisoformat(f["date"])), "description": desc, "normalized_description": normalize_description(desc),
        "merchant_key": merchant_key(desc), "amount": f["amount"], "type": kind, "category": category, "notes": f"Payment slip: {it['file']}",
        "source": "slip", "import_id": batch_id, "fingerprint": fingerprint(f), "currency": f["currency"], "created_at": now, "updated_at": now,
        "slip": {"file": it["file"], "extraction": it["method"], "time": f.get("time"), "payment_method": f.get("payment_method"), "reference": f.get("reference"),
                 "provider": f.get("provider"), "status": f.get("status"), "ocr_confidence": it.get("ocr_confidence"), "verified": bool(it["verified"]), "edited": it["edited"]},
    }


def public_item(it: dict, with_text: bool = False) -> dict:
    out = {k: v for k, v in it.items() if k != "raw_text"}
    out["import_status_label"] = STATUS_LABELS.get(it.get("import_status", ""), "")
    out["default_selected"] = it.get("import_status") == "ready"
    if with_text:
        out["raw_text"] = it.get("raw_text", "")
    return out


def summarize(items: list[dict]) -> dict:
    ok = [i for i in items if i["ok"]]
    counted = [i for i in ok if i["fields"].get("status") == "successful" and i["fields"].get("currency") == "INR" and i["fields"].get("amount")
               and not (i["duplicate"] and i["duplicate"]["kind"] in ("in_batch", "already_imported"))]
    return {
        "total_files": len(items), "processed": len(ok), "failed": len(items) - len(ok), "transactions": len(ok),
        "requiring_review": sum(1 for i in ok if i["review_required"]), "possible_duplicates": sum(1 for i in ok if i["duplicate"]),
        "ready": sum(1 for i in ok if i["import_status"] == "ready"), "imported": sum(1 for i in ok if i["import_status"] == "imported"),
        "successful_expense_paise": sum(i["fields"]["amount"] for i in counted if i["fields"].get("type") == "expense"),
        "successful_income_paise": sum(i["fields"]["amount"] for i in counted if i["fields"].get("type") == "income"),
    }


def cleanup_expired(db) -> int:
    """Delete expired batches (the TTL index does this too, but only roughly every minute and only while the instance runs)."""
    return db.slip_batches.delete_many({"expires_at": {"$lt": now_utc()}}).deleted_count


# ------------------------------------------------------------------ Excel workbook (in memory)
def _safe(v):
    return "'" + v if isinstance(v, str) and v and v[0] in "=+-@\t\r" else v


def build_workbook(batch: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    items = batch["items"]
    sm = summarize(items)
    wb = Workbook()
    head_fill, head_font = PatternFill("solid", fgColor="1E3A8A"), Font(bold=True, color="FFFFFF")
    review_fill = PatternFill("solid", fgColor="FEF3C7")

    def style_header(ws, widths):
        for c in ws[1]:
            c.fill, c.font, c.alignment = head_fill, head_font, Alignment(vertical="center", wrap_text=True)
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions

    ws = wb.active
    ws.title = "Extracted Transactions"
    ws.append(["Source File", "Transaction Date", "Transaction Time", "Amount (INR)", "Transaction Type", "Merchant / Recipient", "Sender", "Payment Method", "Transaction Reference",
               "Bank / Provider", "Status", "Description", "OCR Confidence", "Review Required", "Import Status", "Currency", "Original Amount (non-INR)", "Extraction Method",
               "Duplicate Note", "Missing / Uncertain Fields"])
    for it in (i for i in items if i["ok"]):
        f = it["fields"]
        amt = None if f.get("amount") is None else Decimal(f["amount"]) / 100
        inr = f.get("currency") == "INR"
        unc = [f"{k} (uncertain)" for k in it["uncertain"]]
        ws.append([_safe(it["file"]), date.fromisoformat(f["date"]) if f.get("date") else None, f.get("time"), amt if inr else None, f.get("type"), _safe(f.get("merchant")),
                   _safe(f.get("sender")), f.get("payment_method"), _safe(f.get("reference")), f.get("provider"), f.get("status"), _safe(f.get("description")),
                   it["ocr_confidence"] if it["ocr_confidence"] is not None else "n/a (digital text)", "Yes" if it["review_required"] else "No", STATUS_LABELS.get(it["import_status"], ""),
                   f.get("currency"), None if inr else amt, it["method"], _safe(it["duplicate"]["detail"]) if it["duplicate"] else None, ", ".join(it["missing"] + unc) or None])
        row = ws.max_row
        ws.cell(row, 2).number_format = "dd-mmm-yyyy"
        ws.cell(row, 4).number_format = ws.cell(row, 17).number_format = "#,##0.00"
        for c in ws[row]:
            c.alignment = Alignment(vertical="top", wrap_text=True)
            if it["review_required"]:
                c.fill = review_fill
    style_header(ws, [30, 14, 10, 14, 12, 28, 22, 16, 26, 20, 12, 36, 14, 12, 28, 9, 16, 14, 44, 34])

    sh = wb.create_sheet("Extraction Summary")
    rows = [("Total files uploaded", sm["total_files"]), ("Successfully processed files", sm["processed"]), ("Failed files", sm["failed"]), ("Extracted transactions", sm["transactions"]),
            ("Transactions requiring review", sm["requiring_review"]), ("Possible duplicates", sm["possible_duplicates"]),
            ("Total successful expense amount (INR, excluding duplicates)", Decimal(sm["successful_expense_paise"]) / 100),
            ("Total successful income amount (INR, excluding duplicates)", Decimal(sm["successful_income_paise"]) / 100),
            ("Batch ID", batch["_id"].__str__()), ("Generated (UTC)", now_utc().strftime("%Y-%m-%d %H:%M")),
            ("Note", "Values were read by OCR from images/PDFs and may contain mistakes. Nothing is saved to FinanceAI until you confirm the import.")]
    sh.append(["Measure", "Value"])
    for r in rows:
        sh.append(list(r))
    for r in (7, 8):
        sh.cell(r, 2).number_format = "#,##0.00"
    style_header(sh, [62, 40])
    sh.auto_filter.ref = None

    se = wb.create_sheet("Errors")
    se.append(["Filename", "Error Reason", "Processing Status"])
    for it in (i for i in items if not i["ok"]):
        se.append([_safe(it["file"]), it["error"], "Failed"])
    style_header(se, [40, 80, 18])

    st = wb.create_sheet("Source Text")
    st.append(["Source File", "Extraction Method", "Text read from the file (first 3,000 characters)"])
    for it in (i for i in items if i["ok"]):
        st.append([_safe(it["file"]), it["method"], _safe(it.get("raw_text", "")[:3000]) or "(removed after import)"])
    style_header(st, [30, 16, 100])
    for row in st.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

