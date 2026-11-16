"""Turn raw OCR/PDF text from a payment slip into structured transaction fields.

Rules of this module:
  * Never invent a value. A field that is not visible is None.
  * A value that was guessed or looks unreliable is listed in `uncertain` with the reason, so the UI can ask the user.
  * Money is parsed with Decimal into integer minor units (paise); no floats.
Pure functions: no I/O, fully unit-testable without an OCR engine.
"""
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

MONTHS = {m: i for i, names in enumerate(
    ["jan january", "feb february", "mar march", "apr april", "may", "jun june", "jul july", "aug august", "sep sept september", "oct october", "nov november", "dec december"], start=1)
    for m in names.split()}
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

# Tesseract's English model often reads the rupee sign as one of these (or drops it). Treated as "a currency mark was here".
_RUPEE_MISREADS = "=%?<>£¥€®@&~*#"
_AMOUNT_NUM = r"(\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
_CUR_MARK = rf"(?:(₹|rs\.?|inr|rupees?)|([{re.escape(_RUPEE_MISREADS)}]))"
_NEG_LABEL = re.compile(r"balance|available|limit|fee|charge|tax|gst|cashback|reward|points|discount|saved|total due|outstanding|closing|opening|\bmrp\b|convenience", re.I)
_POS_LABEL = re.compile(r"(amount\s*(paid|debited|credited|received|transferred|sent)?|total(\s*amount)?|grand\s*total|net\s*amount|paid|payment\s*amount|bill\s*amount|sale\s*amount|you\s*(paid|sent|received))\s*[:\-]?\s*$", re.I)

APPS = [("PhonePe", r"phone\s?pe"), ("Google Pay", r"google\s?pay|g\s?pay|gpay"), ("Paytm", r"paytm"), ("BHIM", r"\bbhim\b"), ("Amazon Pay", r"amazon\s?pay"),
        ("CRED", r"\bcred\b"), ("MobiKwik", r"mobikwik"), ("Freecharge", r"freecharge")]
BANKS = [("HDFC Bank", r"hdfc"), ("ICICI Bank", r"icici"), ("State Bank of India", r"\bsbi\b|state bank of india"), ("Axis Bank", r"\baxis\b"), ("Kotak Mahindra Bank", r"kotak"),
         ("Yes Bank", r"yes\s?bank"), ("Punjab National Bank", r"\bpnb\b|punjab national"), ("Bank of Baroda", r"bank of baroda|\bbob\b"), ("Canara Bank", r"canara"),
         ("IDFC FIRST Bank", r"idfc"), ("IndusInd Bank", r"indusind"), ("Federal Bank", r"federal\s?bank"), ("Union Bank of India", r"union bank")]
_INR_RAILS = re.compile(r"\b(upi|imps|neft|rtgs|bhim|phone\s?pe|google\s?pay|gpay|paytm)\b", re.I)
_GENERIC_LINE = re.compile(r"payment|receipt|successful|success|transaction|statement|invoice|tax|netbanking|upi|google pay|phonepe|paytm|bhim|debit|credit|thank|powered|status|date|time|amount|total|bill|fund|transfer|refund|failed|pending|completed|approved|order|ref|store\s*\d|card|pos\b|^\W*$", re.I)


# ------------------------------------------------------------------ amount / currency
def _to_paise(token: str) -> int | None:
    try:
        d = Decimal(token.replace(",", ""))
    except InvalidOperation:
        return None
    if d <= 0 or d > Decimal("99999999.99"):
        return None
    return int((d * 100).quantize(Decimal(1)))


def find_amount(lines: list[str]) -> tuple[int | None, str | None, str | None]:
    """Return (paise, currency_evidence 'explicit'|None, uncertainty_reason). Chooses the best-scoring candidate."""
    cands: list[tuple[int, int, bool, int]] = []  # (score, paise, explicit_currency, line_idx)
    for i, line in enumerate(lines):
        label_here = bool(_POS_LABEL.search(line.split(":")[0] + ":")) if ":" in line else False
        neg = bool(_NEG_LABEL.search(line))
        for m in re.finditer(rf"{_CUR_MARK}\s*[-+]?\s*{_AMOUNT_NUM}", line, re.I):
            tok = m.group(3)
            if re.fullmatch(r"\d{9,}", tok):  # a long digit run is an id, not money
                continue
            p = _to_paise(tok)
            if p is None:
                continue
            explicit = bool(m.group(1))
            score = 10 if explicit else (6 if ("." in tok or "," in tok or len(tok) >= 3) else 3)
            if label_here or (i > 0 and _POS_LABEL.search(lines[i - 1])):
                score += 4
            if neg:
                score -= 12
            cands.append((score, p, explicit, i))
        # "Amount: 486.00" / "TOTAL 310.00" with no currency mark at all
        m = re.search(rf"(?:amount(?:\s*(?:paid|debited|credited|received|transferred|sent))?|total(?:\s*amount)?|grand\s*total|net\s*amount|paid|you\s*paid|you\s*sent)\s*[:\-]?\s*[-+]?\s*{_AMOUNT_NUM}\b", line, re.I)
        if m and not re.fullmatch(r"\d{9,}", m.group(1)):
            p = _to_paise(m.group(1))
            if p is not None:
                cands.append((8 - (12 if neg else 0), p, False, i))
        # a line that is only a number with decimals (typical big amount on app screenshots)
        m = re.fullmatch(rf"\s*[-+]?\s*{_AMOUNT_NUM}\s*", line)
        if m and "." in m.group(1):
            p = _to_paise(m.group(1))
            if p is not None:
                cands.append((4, p, False, i))
    cands = [c for c in cands if c[0] >= 4]
    if not cands:
        return None, None, None
    cands.sort(key=lambda c: (-c[0], c[3]))
    best = cands[0]
    top = {c[1] for c in cands if c[0] == best[0]}
    others = {c[1] for c in cands if c[0] >= best[0] - 2} - {best[1]}
    reason = None
    if len(top) > 1 or others:
        shown = ", ".join(f"{v // 100}.{v % 100:02d}" for v in sorted(top | others)[:4])
        reason = f"several different amounts were found ({shown}); please confirm the correct one"
    return best[1], ("explicit" if best[2] else None), reason


def find_currency(text: str, explicit_amount_mark: bool) -> tuple[str | None, str | None]:
    """INR only on clear evidence: a rupee mark/code, or an INR-only payment rail. Otherwise None (never assumed)."""
    low = text.lower()
    if explicit_amount_mark or re.search(r"₹|\brs\.?\s*\d|\binr\b|\brupees?\b", low):
        return "INR", None
    if re.search(r"\busd\b|\$\s*\d", low):
        return "USD", None
    if re.search(r"\beur\b|€\s*\d", low):
        return "EUR", None
    if re.search(r"\bgbp\b|£\s*\d", low):
        return "GBP", None
    if _INR_RAILS.search(text):
        return "INR", None  # UPI/IMPS/NEFT/RTGS only move rupees
    return None, "no currency symbol or code could be read on the slip"


# ------------------------------------------------------------------ date / time
def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y if y > 99 else 2000 + y, m, d)
    except ValueError:
        return None


def find_dates(line: str) -> list[tuple[date, bool]]:
    """All valid dates in a line as (date, ambiguous_day_month_order)."""
    out: list[tuple[date, bool]] = []
    for m in re.finditer(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", line):
        d = _mk(int(m[1]), int(m[2]), int(m[3]))
        if d:
            out.append((d, False))
    for m in re.finditer(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?[\s\-/,.]*{_MON}[a-z]*\.?[\s\-/,.]*(\d{{2,4}})\b", line, re.I):
        d = _mk(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
        if d:
            out.append((d, False))
    for m in re.finditer(rf"\b{_MON}[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", line, re.I):
        d = _mk(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
        if d:
            out.append((d, False))
    for m in re.finditer(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4}|\d{2})\b", line):
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if a > 12 and b <= 12:
            d, amb = _mk(y, b, a), False
        elif b > 12 and a <= 12:
            d, amb = _mk(y, a, b), False  # clearly month-first
        else:
            d, amb = _mk(y, b, a), (a != b)  # India: assume day-first, but say so when it could be month-first
        if d and not any(d == x for x, _ in out):
            out.append((d, amb))
    return out


def find_date(lines: list[str], today: date) -> tuple[date | None, str | None]:
    found: list[tuple[int, date, bool, int]] = []  # (priority, date, ambiguous, idx)
    for i, line in enumerate(lines):
        for d, amb in find_dates(line):
            pr = 0 if re.search(r"\bdate\b|\bon\b|paid on|txn|transaction|at\b", line, re.I) else 1
            if d < date(2000, 1, 1):
                continue
            found.append((pr, d, amb, i))
    if not found:
        return None, None
    found.sort(key=lambda f: (f[0], f[3]))
    best = found[0]
    distinct = {f[1] for f in found if f[0] == best[0]}
    reason = None
    if len(distinct) > 1:
        reason = "more than one date was found on the slip; please confirm the transaction date"
    elif best[2]:
        reason = "day and month could be in either order; read as day-first"
    elif best[1] > today + timedelta(days=1):
        reason = "the date is in the future"
    return best[1], reason


def find_time(lines: list[str], date_line: int | None = None) -> str | None:
    pat = re.compile(r"\b(\d{1,2}):(\d{2})(?::\d{2})?\s*([AaPp])\.?[Mm]?\.?(?![A-Za-z])|\b(\d{1,2}):(\d{2})(?::\d{2})?\b")
    order = list(range(len(lines)))
    if date_line is not None:
        order = [date_line] + [i for i in order if i != date_line]
    for i in order:
        for m in pat.finditer(lines[i]):
            h, mi, ap = (m[1], m[2], m[3]) if m[1] else (m[4], m[5], None)
            h, mi = int(h), int(mi)
            if ap:
                if not 1 <= h <= 12:
                    continue
                h = h % 12 + (12 if ap.lower() == "p" else 0)
            if h > 23 or mi > 59:
                continue
            return f"{h:02d}:{mi:02d}"
    return None


# ------------------------------------------------------------------ status / type / method / provider
def find_status(low: str) -> str:
    if re.search(r"\b(failed|failure|declined|unsuccessful|not successful|rejected|cancell?ed)\b", low):
        return "failed"
    if re.search(r"\b(pending|processing|in progress|being processed|awaiting|initiated)\b", low) and not re.search(r"refund initiated", low):
        return "pending"
    if re.search(r"refund(ed)?\s*(successful|processed|completed|id|from|of|amount)|\brefunded\b|money returned", low) and "policy" not in low:
        return "refunded"
    if re.search(r"\b(success(ful(ly)?)?|completed|approved|paid|debited|credited|received|sent|done)\b", low):
        return "successful"
    return "unknown"


def find_type(low: str, status: str) -> str | None:
    if status == "refunded":
        return "refund"
    if re.search(r"(self|own)[\s\-]*(account|a/c|acc\b|transfer)|transfer(red)?\s*to\s*(self|own|your own)|between\s*(your\s*)?accounts", low):
        return "transfer"
    paid = re.search(r"paid to|payment to|sent to|transfer(red)? to|debited from|debited|beneficiary|paid\b|you paid|you sent|purchase|card sale|\bsale\b|bill|invoice|receipt|total|amount paid", low)
    received = re.search(r"received from|money received|payment received|you received|credited to|credit alert|amount credited", low)
    if received and not re.search(r"paid to|sent to|debited|beneficiary|you paid|you sent", low):
        return "income"
    if paid:
        return "expense"
    return None


def find_method(low: str, lines: list[str]) -> str | None:
    for line in lines:
        m = re.search(r"(?:payment\s*mode|mode\s*of\s*payment|paid\s*via|payment\s*method|transfer\s*mode)\s*[:\-]\s*(.+)", line, re.I)
        if m:
            v = m[1].lower()
            for name, pat in (("UPI", r"upi"), ("Card", r"card|visa|master"), ("Bank transfer", r"neft|imps|rtgs|transfer|net\s?banking"), ("Cash", r"cash"), ("Wallet", r"wallet")):
                if re.search(pat, v):
                    return name
    if re.search(r"\b(visa|mastercard|master card|rupay|debit card|credit card|card sale|card ending|card:|card no)\b|\*{2,}\s*\d{4}", low):
        return "Card"
    if re.search(r"\b(upi|bhim|gpay|google pay|phonepe|phone pe|paytm)\b", low):
        return "UPI"
    if re.search(r"\b(neft|imps|rtgs|bank transfer|fund transfer|net\s?banking)\b", low):
        return "Bank transfer"
    if re.search(r"\bwallet\b", low):
        return "Wallet"
    if re.search(r"\bcash\b(?!\s*back)", low):
        return "Cash"
    return None


def find_provider(text: str) -> str | None:
    for name, pat in APPS:
        if re.search(pat, text, re.I):
            return name
    for name, pat in BANKS:
        if re.search(pat, text, re.I):
            return name
    return None


# ------------------------------------------------------------------ reference
_REF_PATTERNS = [
    ("UTR", r"\bUTR(?:\s*(?:No|Number|#))?\.?\s*[:\-#]?\s*(?:\|\s*)?([A-Z0-9]{8,35})"),
    ("UPI ref", r"\bUPI\s*(?:Ref(?:erence)?|Transaction|Txn)\s*(?:No\.?|ID|Number)?\s*[:\-#]?\s*(?:\|\s*)?([A-Z0-9]{8,35})"),
    ("RRN", r"\bRRN\b\s*[:\-#]?\s*(?:\|\s*)?(\d{8,16})"),
    ("Transaction ID", r"\b(?:Transaction|Txn|Trans)\.?\s*(?:ID|No\.?|Ref(?:erence)?(?:\s*(?:No\.?|ID))?|Number)\s*[:\-#]?\s*(?:\|\s*)?([A-Z0-9]{8,35})"),
    ("Refund ID", r"\bRefund\s*(?:ID|Ref(?:erence)?(?:\s*No\.?)?)\s*[:\-#]?\s*(?:\|\s*)?([A-Z0-9]{8,35})"),
    ("Order ID", r"\bOrder\s*(?:ID|No\.?|Number)\s*[:\-#]?\s*(?:\|\s*)?([A-Z0-9]{8,35})"),
    ("Reference", r"\b(?:Ref(?:erence)?\.?)\s*(?:No\.?|ID|Number)\s*[:\-#]?\s*(?:\|\s*)?([A-Z0-9]{8,35})"),
]


def find_reference(lines: list[str]) -> tuple[str | None, str | None]:
    joined = " | ".join(lines)
    for _name, pat in _REF_PATTERNS:
        for m in re.finditer(pat, joined, re.I):
            tok = m[1].upper()
            if sum(c.isdigit() for c in tok) < 4:
                continue
            reason = None
            frag = re.match(r"\s([A-Za-z0-9]{1,2})(?=\s*(?:\||$))", joined[m.end(1):])
            if frag:  # OCR often splits the last character(s) of a long id off with a space
                tok += frag[1].upper()
                reason = "the reference looked split by the OCR and was joined; please compare it with the slip"
            return tok, reason
    return None, None


# ------------------------------------------------------------------ names
def _clean_name(v: str) -> str | None:
    v = re.sub(r"\(.*?\)|\[.*?\]", " ", v)  # drop UPI ids / masks in brackets
    v = re.split(r"\s+(?:rs\.?|inr|₹)\s*\d|\s\|\s|\s{3,}", v, flags=re.I)[0]
    v = re.sub(r"[^\w&.'/\- ]", " ", v)
    v = re.sub(r"\s+", " ", v).strip(" .-:/")
    if len(v) < 2 or sum(c.isalpha() for c in v) < 2:
        return None
    if re.fullmatch(r"[\d\s.\-x]+", v, re.I) or re.search(r"\b(successful|success|failed|pending|completed|your|bank account|a/c|xxxx)\b", v, re.I):
        return None
    if re.search(r"\b(hdfc|icici|sbi|axis|kotak)\b", v, re.I) and re.search(r"\d{3,}|bank", v, re.I):
        return None  # that is the user's own account, not a counterparty
    return v[:80]


def _labelled(lines: list[str], labels: str, *, line_start_only: bool = False) -> str | None:
    anchor = "^" if line_start_only else r"(?:^|\b)"
    pat = re.compile(rf"{anchor}(?:{labels})\b\s*[:\-]?\s*(.*)$", re.I)
    for i, line in enumerate(lines):
        m = pat.search(line)
        if not m:
            continue
        v = m[1].strip()
        if not v and i + 1 < len(lines):
            v = lines[i + 1]
        name = _clean_name(v) if v else None
        if name:
            return name
    return None


def find_recipient(lines: list[str]) -> str | None:
    v = _labelled(lines, r"paid to|payment to|sent to|transferred to|transfer to|beneficiary(?:\s*name)?|payee(?:\s*name)?|merchant(?:\s*name)?|biller(?:\s*name)?|refund from|paid at|received by")
    return v or _labelled(lines, r"to", line_start_only=True)


def find_sender(lines: list[str], tx_type: str | None) -> str | None:
    v = _labelled(lines, r"received from|credit from|credited by|sender(?:\s*name)?|paid by") if tx_type in ("income", "refund", "transfer") else _labelled(lines, r"sender(?:\s*name)?|paid by")
    if not v and tx_type == "income":
        v = _labelled(lines, r"from", line_start_only=True)
    return v


def find_header_merchant(lines: list[str]) -> str | None:
    for line in lines[:4]:
        if _GENERIC_LINE.search(line) or sum(c.isdigit() for c in line) > 4:
            continue
        n = _clean_name(line)
        if n and len(n) >= 4:
            return n
    return None


def find_remarks(lines: list[str]) -> str | None:
    return _labelled(lines, r"remarks?|note|message|narration|description|payment for|purpose")


# ------------------------------------------------------------------ main entry
def parse_slip(text: str, today: date | None = None) -> dict:
    """Return {'fields': {...}, 'uncertain': {field: reason}}. Missing values are None, never guesses."""
    today = today or datetime.now(timezone.utc).date()
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    low = " ".join(lines).lower()
    unc: dict[str, str] = {}
    notes: dict[str, str] = {}  # informational only: shown to the user, do not force a review

    status = find_status(low)
    tx_type = find_type(low, status)
    amount, explicit, amount_reason = find_amount(lines)
    if amount_reason:
        unc["amount"] = amount_reason
    currency, cur_reason = find_currency(text or "", explicit == "explicit")
    if amount is not None and currency is None and cur_reason:
        unc["currency"] = cur_reason
    d, d_reason = find_date(lines, today)
    if d_reason:
        (notes if d_reason.startswith("day and month") else unc)["date"] = d_reason
    date_line = next((i for i, ln in enumerate(lines) if d and any(x == d for x, _ in find_dates(ln))), None)
    time = find_time(lines, date_line)
    ref, ref_reason = find_reference(lines)
    if ref_reason:
        unc["reference"] = ref_reason
    method = find_method(low, lines)
    provider = find_provider(text or "")

    merchant = sender = None
    labelled_recipient = False
    if tx_type != "income":
        merchant = find_recipient(lines)
        labelled_recipient = merchant is not None
        if merchant is None and tx_type in ("expense", None):
            merchant = find_header_merchant(lines)
            if merchant:
                unc["merchant"] = "taken from the top of the slip, not from a labelled field"
    if tx_type is None and labelled_recipient and not re.search(r"received|credited to|credit alert", low):
        tx_type = "expense"  # a labelled recipient ("To X", "Paid to X") means the money went out
    sender = find_sender(lines, tx_type)
    if tx_type == "income" and sender is None:
        sender = find_header_merchant(lines)
        if sender:
            unc["sender"] = "taken from the top of the slip, not from a labelled field"

    remarks = find_remarks(lines)
    if remarks:
        desc = remarks
    elif tx_type == "income" and sender:
        desc = f"Received from {sender}" + (f" via {method}" if method else "")
    elif tx_type == "refund" and merchant:
        desc = f"Refund from {merchant}"
    elif merchant:
        desc = f"Paid to {merchant}" + (f" via {method}" if method else "")
    else:
        desc = None
    if tx_type is None and (amount is not None or d is not None):
        unc["type"] = "could not tell whether this is money paid or received"

    fields = {"date": d.isoformat() if d else None, "time": time, "amount": amount, "currency": currency, "type": tx_type, "merchant": merchant, "sender": sender,
              "payment_method": method, "reference": ref, "provider": provider, "status": status, "description": desc}
    if status == "unknown":
        unc["status"] = "the slip does not say whether the payment succeeded"
    return {"fields": fields, "uncertain": unc, "notes": notes}
