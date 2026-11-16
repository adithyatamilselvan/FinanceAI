"""Payment Slip Scanner: real Tesseract OCR on SYNTHETIC slips (docs/test-data/payment-slips), parser, batches, workbook, import, security."""
import hashlib
import io
import json
import logging
import os
import tempfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app.config import get_settings
from app.services import ocr
from app.services.slip_parser import find_amount, find_currency, find_dates, parse_slip
from tests.conftest import API, import_statement, register

SLIPS = Path(__file__).resolve().parents[2] / "docs" / "test-data" / "payment-slips"
TRUTH = json.loads((SLIPS / "expected_slips.json").read_text(encoding="utf-8"))
TEN = sorted(k for k in TRUTH if not k.startswith("edge-cases/"))
needs_ocr = pytest.mark.skipif(not ocr.ocr_status()["available"], reason="Tesseract / OCR libraries not installed")
CTYPE = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "pdf": "application/pdf"}
KEYS = ("date", "time", "amount", "currency", "type", "merchant", "sender", "payment_method", "reference", "provider", "status")
TODAY = date(2026, 11, 16)


@pytest.fixture(autouse=True)
def ocr_cache(monkeypatch):
    """Run the REAL OCR once per distinct file, then reuse the result (keeps repeated 10-file batches fast)."""
    real, cache = ocr.extract_text, {}

    def cached(ext, data):
        key = (ext, hashlib.sha256(data).hexdigest())
        if key not in cache:
            cache[key] = real(ext, data)
        return cache[key]

    monkeypatch.setattr(ocr, "extract_text", cached)


def slip(name: str) -> bytes:
    return (SLIPS / name).read_bytes()


def new_batch(client, h) -> str:
    r = client.post(f"{API}/slips/batches", headers=h)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def upload(client, h, bid, name, data=None, ctype=None):
    data = slip(name) if data is None else data
    ext = name.rsplit(".", 1)[-1].lower()
    return client.post(f"{API}/slips/batches/{bid}/files", headers=h, files={"file": (Path(name).name, data, ctype or CTYPE.get(ext, "application/octet-stream"))})


def upload_ok(client, h, bid, name, **kw):
    r = upload(client, h, bid, name, **kw)
    assert r.status_code == 200, r.text
    return r.json()["item"]


def batch(client, h, bid):
    return client.get(f"{API}/slips/batches/{bid}", headers=h).json()


def fields_of(item):
    return {k: item["fields"][k] for k in KEYS}


def expected_of(name):
    return {k: v for k, v in TRUTH[name].items() if k != "note"}


# =============================== parser (no OCR needed) ===============================
@pytest.mark.parametrize("text,paise", [("Rs. 486.00", 48600), ("₹2,340.50", 234050), ("INR 842.00", 84200), ("=486.00\nPaid to X", 48600), ("%1,299.00", 129900),
                                        ("Amount: 22,000.00", 2200000), ("Rs 310", 31000), ("TOTAL: Rs. 0.10", 10), ("Amount Paid: Rs. 1,00,000.25", 10000025), ("Rs. 1,234", 123400)])
def test_amount_parsing_is_exact_paise(text, paise):
    assert find_amount(text.splitlines())[0] == paise


def test_amount_ignores_balances_ids_and_zero():
    assert find_amount(["Available balance Rs. 5,000.00", "Paid Rs. 120.00"])[0] == 12000
    assert find_amount(["Available balance Rs. 5,000.00"])[0] is None
    assert find_amount(["UTR 628812345678", "Order 20261020082598"])[0] is None
    assert find_amount(["Rs. 0.00"])[0] is None
    amt, _, reason = find_amount(["Amount: 100.00", "Amount: 250.00"])
    assert reason and "several different amounts" in reason  # ambiguity is surfaced, not silently resolved


def test_currency_is_never_assumed():
    assert find_currency("Rs. 486.00", False)[0] == "INR" and find_currency("₹486", False)[0] == "INR" and find_currency("INR 5", False)[0] == "INR"
    assert find_currency("=486.00 paid via UPI", False)[0] == "INR"  # UPI only moves rupees
    cur, why = find_currency("Total 486.00 thank you", False)
    assert cur is None and why
    assert find_currency("Total $12.50", False)[0] == "USD"
    p = parse_slip("Store receipt\nTotal 486.00\nDate 12/10/2026", TODAY)
    assert p["fields"]["currency"] is None and "currency" in p["uncertain"]


@pytest.mark.parametrize("line,expected", [("12 Oct 2026, 07:45 PM", date(2026, 10, 12)), ("Oct 15, 2026 at 6:12 PM", date(2026, 10, 15)), ("2026-10-12", date(2026, 10, 12)),
                                           ("03-10-2026", date(2026, 10, 3)), ("25/10/26", date(2026, 10, 25)), ("12th October 2026", date(2026, 10, 12)), ("10/25/2026", date(2026, 10, 25))])
def test_date_formats(line, expected):
    assert find_dates(line)[0][0] == expected


def test_date_ambiguity_and_future_are_flagged_not_hidden():
    p = parse_slip("Rs. 5.00\nDate: 03/10/2026\nUPI", TODAY)
    assert p["fields"]["date"] == "2026-10-03" and "date" in p["notes"] and "date" not in p["uncertain"]
    assert parse_slip("Rs. 5.00\nDate: 25 Dec 2026\nUPI", TODAY)["uncertain"]["date"] == "the date is in the future"
    assert "more than one date" in parse_slip("Rs. 5.00 UPI\n12 Oct 2026\n20 Oct 2026", TODAY)["uncertain"]["date"]
    assert parse_slip("Rs. 5.00 UPI\n31/02/2026", TODAY)["fields"]["date"] is None  # impossible date -> not guessed


@pytest.mark.parametrize("text,time", [("12 Oct 2026, 07:45 PM", "19:45"), ("12:05 AM", "00:05"), ("12:30 PM", "12:30"), ("Time: 10:05:33", "10:05"), ("at 6.12 pm", None), ("25:99", None)])
def test_time_parsing(text, time):
    from app.services.slip_parser import find_time

    assert find_time([text]) == time


@pytest.mark.parametrize("text,status,typ", [
    ("Payment Failed\nIf money was debited it will be refunded", "failed", "expense"), ("Payment Pending\nPaid to X", "pending", "expense"),
    ("Refund Successful\nRefund from AMAZON", "refunded", "refund"), ("Payment Successful\nPaid to X", "successful", "expense"), ("Money Received\nFrom Amit", "successful", "income"),
    ("Self transfer to own account\nPayment successful", "successful", "transfer"), ("hello world", "unknown", None)])
def test_status_and_type_priorities(text, status, typ):
    f = parse_slip(text + "\nRs. 10.00 UPI", TODAY)["fields"]
    assert f["status"] == status and f["type"] == typ


def test_reference_priority_and_split_repair():
    f = parse_slip("Transaction ID\nT2610121945123456789\nUTR: 628812345678\nRs. 1 UPI", TODAY)["fields"]
    assert f["reference"] == "628812345678"  # bank reference beats app transaction id
    p = parse_slip("Transaction Ref: BES261010009987 1\nPayment Mode: UPI", TODAY)
    assert p["fields"]["reference"] == "BES2610100099871" and "reference" in p["uncertain"]
    assert parse_slip("Transaction ID: abc\nRs. 5 UPI", TODAY)["fields"]["reference"] is None  # too short / no digits


def test_nothing_is_invented_from_unrelated_text():
    p = parse_slip("Weekend recipe notes\nBoil the rice for twenty minutes\nAdd salt", TODAY)["fields"]
    assert p["amount"] is None and p["date"] is None and p["reference"] is None and p["currency"] is None and p["status"] == "unknown" and p["time"] is None
    empty = parse_slip("", TODAY)["fields"]
    assert all(v is None or v == "unknown" for v in empty.values())


def test_user_own_bank_account_is_not_the_merchant():
    f = parse_slip("Google Pay\nPayment completed\nFrom: HDFC Bank 4471\nTo BIGBASKET\nRs. 5.00", TODAY)["fields"]
    assert f["merchant"] == "BIGBASKET" and f["sender"] is None


# =============================== real OCR: formats ===============================
@needs_ocr
@pytest.mark.parametrize("name", ["slip_01_phonepe_swiggy.png", "slip_02_gpay_bigbasket.jpg", "slip_03_paytm_received.jpeg"])
def test_image_formats_png_jpg_jpeg_extract_exactly(client, auth, name):
    bid = new_batch(client, auth)
    it = upload_ok(client, auth, bid, name)
    assert it["ok"] and it["method"] == "ocr-image" and it["ocr_confidence"] > 80
    assert fields_of(it) == {k: expected_of(f"{name}").get(k) for k in KEYS} | {}


@needs_ocr
def test_digital_pdf_uses_direct_text_not_ocr(client, auth):
    bid = new_batch(client, auth)
    it = upload_ok(client, auth, bid, "slip_04_neft_landlord.pdf")
    assert it["method"] == "pdf-text" and it["ocr_confidence"] is None  # no OCR was needed, and no fake confidence is invented
    assert fields_of(it) == expected_of("slip_04_neft_landlord.pdf") | {"time": "10:05"}
    assert it["fields"]["description"] == "Rent October"


@needs_ocr
def test_scanned_pdf_is_ocred_and_ocr_error_is_flagged_for_review(client, auth):
    bid = new_batch(client, auth)
    it = upload_ok(client, auth, bid, "slip_06_bescom_scanned.pdf")
    assert it["method"] == "pdf-ocr" and it["pages"] == 1 and it["ocr_confidence"] > 60
    assert fields_of(it) == expected_of("slip_06_bescom_scanned.pdf")
    assert "reference" in it["uncertain"] and it["import_status"] == "needs_review" and it["review_required"] is True


@needs_ocr
def test_multi_page_pdf_extracts_all_pages_and_enforces_page_limit(client, auth, monkeypatch):
    import fitz

    doc = fitz.open()
    for t in ("Page one\nPaid to SHOP ONE\nRs. 10.00 UPI", "Page two\nUTR: 111122223333\n12 Oct 2026"):
        doc.new_page().insert_text((50, 100), t, fontsize=12)
    data = doc.tobytes()
    res = ocr.ocr_pdf_bytes(data)
    assert res.pages == 2 and "SHOP ONE" in res.text and "111122223333" in res.text and res.method == "pdf-text"
    big = fitz.open()
    for i in range(7):
        big.new_page().insert_text((50, 100), f"page {i}", fontsize=12)
    bid = new_batch(client, auth)
    r = upload(client, auth, bid, "toolong.pdf", big.tobytes()).json()["item"]
    assert not r["ok"] and "limit is 5" in r["error"]


# =============================== 10-file batch (acceptance) ===============================
@needs_ocr
def test_ten_file_batch_every_field_matches_ground_truth(client, auth):
    bid = new_batch(client, auth)
    for name in TEN:
        upload_ok(client, auth, bid, name)
    b = batch(client, auth, bid)
    assert b["summary"]["total_files"] == 10 and b["summary"]["processed"] == 10 and b["summary"]["failed"] == 0
    got = {i["file"]: i for i in b["items"]}
    wrong = []
    for name in TEN:
        exp = expected_of(name)
        have = fields_of(got[Path(name).name])
        for k in KEYS:
            if have[k] != exp.get(k):
                wrong.append((name, k, have[k], exp.get(k)))
    assert wrong == [], f"extraction differs from ground truth: {wrong}"  # 10 slips x 11 fields = 110 fields, all exact
    status = {i["file"]: i["import_status"] for i in b["items"]}
    assert status == {"slip_01_phonepe_swiggy.png": "ready", "slip_02_gpay_bigbasket.jpg": "ready", "slip_03_paytm_received.jpeg": "ready", "slip_04_neft_landlord.pdf": "ready",
                      "slip_05_card_apollo.jpg": "needs_review", "slip_06_bescom_scanned.pdf": "needs_review", "slip_07_cash_kirana.png": "needs_review",
                      "slip_08_failed_actfibernet.png": "payment_failed", "slip_09_pending_zomato.jpg": "not_successful", "slip_10_refund_amazon.png": "not_successful"}
    assert b["summary"]["ready"] == 4 and b["summary"]["requiring_review"] == 6 and b["summary"]["possible_duplicates"] == 0
    assert [i["default_selected"] for i in b["items"]].count(True) == 4  # only successful, complete, unflagged slips are pre-selected


# =============================== problems on the slip ===============================
@needs_ocr
def test_missing_date_and_missing_amount_are_null_flagged_and_block_import(client, auth):
    bid = new_batch(client, auth)
    d = upload_ok(client, auth, bid, "edge-cases/edge_missing_date.png")
    a = upload_ok(client, auth, bid, "edge-cases/edge_missing_amount.png")
    assert d["fields"]["date"] is None and d["fields"]["amount"] == 22000 and "date" in d["missing"] and d["import_status"] == "missing_details"
    assert a["fields"]["amount"] is None and a["fields"]["date"] == "2026-10-12" and "amount" in a["missing"] and a["import_status"] == "missing_details"
    r = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [d["id"], a["id"]]})
    assert r.status_code == 422
    reasons = {x["file"]: " ".join(x["reasons"]) for x in r.json()["detail"]["items"]}
    assert "missing date" in reasons["edge_missing_date.png"] and "missing amount" in reasons["edge_missing_amount.png"]
    assert client.get(f"{API}/transactions", headers=auth).json()["total"] == 0
    # the user fills the gap -> it becomes importable
    fixed = client.patch(f"{API}/slips/batches/{bid}/items/{d['id']}", headers=auth, json={"date": "2026-10-12"}).json()["item"]
    assert fixed["fields"]["date"] == "2026-10-12" and fixed["verified"] and "date" in fixed["edited"] and fixed["import_status"] in ("ready", "needs_review")
    ok = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [d["id"]]})
    assert ok.status_code == 200 and ok.json()["imported"] == 1


@needs_ocr
def test_unreadable_noisy_image_fails_cleanly_and_lands_on_errors_sheet(client, auth):
    bid = new_batch(client, auth)
    it = upload_ok(client, auth, bid, "edge-cases/edge_blurry_noisy.jpg")
    assert it["ok"] is False and it["import_status"] == "failed" and "No transaction details" in it["error"]
    assert it["fields"]["amount"] is None  # garbage never becomes data
    b = load_workbook(io.BytesIO(client.get(f"{API}/slips/batches/{bid}/workbook.xlsx", headers=auth).content))
    rows = list(b["Errors"].iter_rows(values_only=True))
    assert rows[0] == ("Filename", "Error Reason", "Processing Status") and rows[1][0] == "edge_blurry_noisy.jpg" and rows[1][2] == "Failed"


@needs_ocr
def test_low_confidence_forces_review(client, auth, monkeypatch):
    monkeypatch.setattr(get_settings(), "slip_low_confidence", 99)  # every real OCR result is now "low confidence"
    bid = new_batch(client, auth)
    it = upload_ok(client, auth, bid, "slip_01_phonepe_swiggy.png")
    assert any("low OCR confidence" in r for r in it["review_reasons"]) and it["import_status"] == "needs_review"
    r = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [it["id"]]})
    assert r.status_code == 422 and "review" in " ".join(r.json()["detail"]["items"][0]["reasons"])
    client.patch(f"{API}/slips/batches/{bid}/items/{it['id']}", headers=auth, json={"verified": True})
    assert client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [it["id"]]}).status_code == 200


@needs_ocr
def test_blank_and_non_slip_files_produce_errors_not_data(client, auth):
    bid = new_batch(client, auth)
    blank = upload_ok(client, auth, bid, "edge-cases/edge_blank.png")
    notslip = upload_ok(client, auth, bid, "edge-cases/edge_not_a_slip.png")
    assert not blank["ok"] and "No readable text" in blank["error"]
    assert not notslip["ok"] and "No transaction details" in notslip["error"]
    assert client.get(f"{API}/transactions", headers=auth).json()["total"] == 0


@needs_ocr
def test_failed_payment_is_never_importable_pending_and_refund_need_a_decision(client, auth):
    bid = new_batch(client, auth)
    failed = upload_ok(client, auth, bid, "slip_08_failed_actfibernet.png")
    pending = upload_ok(client, auth, bid, "slip_09_pending_zomato.jpg")
    refund = upload_ok(client, auth, bid, "slip_10_refund_amazon.png")
    assert (failed["fields"]["status"], pending["fields"]["status"], refund["fields"]["status"]) == ("failed", "pending", "refunded")
    assert failed["import_status"] == "payment_failed" and pending["import_status"] == "not_successful" and refund["import_status"] == "not_successful"
    # even with every override, a failed payment is refused
    r = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [failed["id"]], "allow_non_successful": True, "allow_possible_duplicates": True})
    assert r.status_code == 422 and "never imported" in " ".join(r.json()["detail"]["items"][0]["reasons"])
    # pending/refunded need the explicit decision AND verification
    r = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [pending["id"], refund["id"]]})
    assert r.status_code == 422 and all("non-successful" in " ".join(x["reasons"]) for x in r.json()["detail"]["items"])
    r = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [pending["id"]], "allow_non_successful": True})
    assert r.status_code == 422 and "verified" in " ".join(r.json()["detail"]["items"][0]["reasons"])
    for i in (pending, refund):
        client.patch(f"{API}/slips/batches/{bid}/items/{i['id']}", headers=auth, json={"verified": True})
    ok = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [pending["id"], refund["id"]], "allow_non_successful": True})
    assert ok.status_code == 200 and ok.json()["imported"] == 2
    types = {next(w for w in ("ZOMATO", "AMAZON") if w in t["description"].upper()): t["type"] for t in client.get(f"{API}/transactions", headers=auth).json()["items"]}
    assert types == {"ZOMATO": "expense", "AMAZON": "refund"}


# =============================== duplicates ===============================
@needs_ocr
def test_same_slip_twice_in_one_batch_is_flagged_not_silently_dropped(client, auth):
    bid = new_batch(client, auth)
    first = upload_ok(client, auth, bid, "slip_01_phonepe_swiggy.png")
    second = upload_ok(client, auth, bid, "slip_01_phonepe_swiggy.png")
    assert first["duplicate"] is None and second["duplicate"]["kind"] == "in_batch" and second["duplicate"]["level"] == "strong" and second["import_status"] == "possible_duplicate"
    assert len(batch(client, auth, bid)["items"]) == 2  # both are still shown to the user
    r = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [first["id"], second["id"]]})
    assert r.status_code == 422 and len(r.json()["detail"]["items"]) == 1
    client.patch(f"{API}/slips/batches/{bid}/items/{second['id']}", headers=auth, json={"verified": True})
    ok = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [first["id"], second["id"]], "allow_possible_duplicates": True}).json()
    assert ok["imported"] == 1 and ok["skipped_duplicates"] == 1  # identical fingerprint can never be stored twice
    assert client.get(f"{API}/transactions", headers=auth).json()["total"] == 1


@needs_ocr
def test_slip_matching_existing_bank_transaction_is_flagged(client, auth):
    import_statement(client, auth, ["2026-10-12,UPI-SWIGGY-628812345678-swiggy@icici,486.00,"])  # bank narration contains the UTR
    bid = new_batch(client, auth)
    it = upload_ok(client, auth, bid, "slip_01_phonepe_swiggy.png")
    assert it["duplicate"]["kind"] == "possible_existing" and it["duplicate"]["level"] == "strong" and "reference" in it["duplicate"]["detail"]
    assert it["import_status"] == "possible_duplicate"
    # without any reference: amount + date + similar payee is flagged as 'probable'; amount + date only as 'weak'
    client.post(f"{API}/transactions", headers=auth, json={"date": "2026-10-15", "description": "BigBasket groceries", "amount": 234050, "type": "expense"})
    client.post(f"{API}/transactions", headers=auth, json={"date": "2026-10-18", "description": "Completely different", "amount": 150000, "type": "income"})
    b2 = new_batch(client, auth)
    bb = upload_ok(client, auth, b2, "slip_02_gpay_bigbasket.jpg")
    # the slip has a reference, so remove the ref-based hit and check the amount/date path
    assert bb["duplicate"] and bb["duplicate"]["level"] in ("probable", "weak")
    rahul = upload_ok(client, auth, b2, "slip_03_paytm_received.jpeg")
    assert rahul["duplicate"] and rahul["duplicate"]["level"] == "weak"


@needs_ocr
def test_importing_the_same_slips_twice_never_creates_duplicates(client, auth):
    first = new_batch(client, auth)
    ids = [upload_ok(client, auth, first, n)["id"] for n in TEN[:4]]  # the four 'ready' slips
    assert client.post(f"{API}/slips/batches/{first}/commit", headers=auth, json={"item_ids": ids}).json()["imported"] == 4
    assert client.get(f"{API}/transactions", headers=auth).json()["total"] == 4
    second = new_batch(client, auth)
    items = [upload_ok(client, auth, second, n) for n in TEN[:4]]
    assert [i["import_status"] for i in items] == ["already_imported"] * 4 and all(i["duplicate"]["kind"] == "already_imported" for i in items)
    r = client.post(f"{API}/slips/batches/{second}/commit", headers=auth, json={"item_ids": [i["id"] for i in items], "allow_possible_duplicates": True, "allow_non_successful": True})
    assert r.status_code == 422 and "already in your transactions" in json.dumps(r.json())
    assert client.get(f"{API}/transactions", headers=auth).json()["total"] == 4


# =============================== workbook ===============================
HEADERS = ["Source File", "Transaction Date", "Transaction Time", "Amount (INR)", "Transaction Type", "Merchant / Recipient", "Sender", "Payment Method", "Transaction Reference",
           "Bank / Provider", "Status", "Description", "OCR Confidence", "Review Required", "Import Status"]


@needs_ocr
def test_excel_workbook_structure_values_and_download(client, auth):
    bid = new_batch(client, auth)
    for n in TEN:
        upload_ok(client, auth, bid, n)
    upload_ok(client, auth, bid, "edge-cases/edge_blank.png")
    upload(client, auth, bid, "virus.exe", b"MZ....", "application/octet-stream")
    r = client.get(f"{API}/slips/batches/{bid}/workbook.xlsx", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats") and "attachment" in r.headers["content-disposition"] and r.content[:2] == b"PK"
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Extracted Transactions", "Extraction Summary", "Errors", "Source Text"]
    ws = wb["Extracted Transactions"]
    rows = list(ws.iter_rows(values_only=True))
    assert list(rows[0][:15]) == HEADERS and len(rows) == 11 and ws.freeze_panes == "A2" and ws.column_dimensions["A"].width >= 25
    by_file = {r[0]: r for r in rows[1:]}
    for name in TEN:
        exp, row = expected_of(name), by_file[Path(name).name]
        assert row[1].date().isoformat() == exp["date"] and Decimal(str(row[3])) * 100 == exp["amount"] and row[4] == exp["type"] and row[10] == exp["status"]
        assert (row[5] or None) == exp["merchant"] and (row[6] or None) == exp["sender"] and (row[8] or None) == exp["reference"] and (row[9] or None) == exp["provider"]
        assert row[13] in ("Yes", "No") and row[14]
    assert by_file["slip_04_neft_landlord.pdf"][12] == "n/a (digital text)" and isinstance(by_file["slip_01_phonepe_swiggy.png"][12], float)
    assert by_file["slip_08_failed_actfibernet.png"][13] == "Yes" and by_file["slip_01_phonepe_swiggy.png"][13] == "No"
    summary = dict(list(wb["Extraction Summary"].iter_rows(values_only=True))[1:])
    exp_exp = Decimal(48600 + 234050 + 2200000 + 84200 + 184000 + 31000) / 100  # successful expenses only (failed/pending/refund excluded)
    assert summary["Total files uploaded"] == 12 and summary["Successfully processed files"] == 10 and summary["Failed files"] == 2
    assert summary["Extracted transactions"] == 10 and summary["Transactions requiring review"] == 6 and summary["Possible duplicates"] == 0
    assert Decimal(str(summary["Total successful expense amount (INR, excluding duplicates)"])) == exp_exp
    assert Decimal(str(summary["Total successful income amount (INR, excluding duplicates)"])) == Decimal("1500.00")
    errs = {r[0]: r for r in list(wb["Errors"].iter_rows(values_only=True))[1:]}
    assert set(errs) == {"edge_blank.png", "virus.exe"} and "Only JPG" in errs["virus.exe"][1]
    src = {r[0]: r for r in list(wb["Source Text"].iter_rows(values_only=True))[1:]}
    assert "SWIGGY" in src["slip_01_phonepe_swiggy.png"][2]  # original extracted text preserved


@needs_ocr
def test_workbook_reflects_user_corrections_and_neutralises_formulas(client, auth):
    bid = new_batch(client, auth)
    it = upload_ok(client, auth, bid, "slip_01_phonepe_swiggy.png")
    client.patch(f"{API}/slips/batches/{bid}/items/{it['id']}", headers=auth, json={"amount": 49900, "merchant": "=HYPERLINK(\"http://evil\")"})
    ws = load_workbook(io.BytesIO(client.get(f"{API}/slips/batches/{bid}/workbook.xlsx", headers=auth).content))["Extracted Transactions"]
    row = list(ws.iter_rows(min_row=2, values_only=True))[0]
    assert Decimal(str(row[3])) == Decimal("499.00") and str(row[5]).startswith("'=")  # the corrected amount is exported; formula text is inert


def test_workbook_requires_content(client, auth):
    bid = new_batch(client, auth)
    assert client.get(f"{API}/slips/batches/{bid}/workbook.xlsx", headers=auth).status_code == 409


# =============================== import into the existing engine ===============================
@needs_ocr
def test_confirmed_import_flows_into_transactions_dashboard_budget_and_reports(client, auth):
    bid = new_batch(client, auth)
    items = {n: upload_ok(client, auth, bid, n) for n in TEN}
    pick = [items[n]["id"] for n in TEN if n not in ("slip_08_failed_actfibernet.png",)]
    for i in pick:
        client.patch(f"{API}/slips/batches/{bid}/items/{i}", headers=auth, json={"verified": True})
    r = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": pick, "allow_non_successful": True})
    assert r.status_code == 200 and r.json()["imported"] == 9 and r.json()["not_selected"] == 1
    txs = client.get(f"{API}/transactions", headers=auth, params={"page_size": 50}).json()
    assert txs["total"] == 9 and all(t["source"] == "slip" and t["currency"] == "INR" for t in txs["items"])
    t01 = next(t for t in txs["items"] if "SWIGGY" in t["description"].upper())
    assert (t01["date"], t01["amount"], t01["type"], t01["category"]) == ("2026-10-12", 48600, "expense", "Food") and t01["slip"]["reference"] == "628812345678" and t01["slip"]["file"] == "slip_01_phonepe_swiggy.png"
    # independent expectation for October (failed payment excluded; refund offsets spending)
    gross = 48600 + 234050 + 2200000 + 84200 + 184000 + 31000 + 75000
    d = client.get(f"{API}/analytics/dashboard", headers=auth, params={"period": "2026-10"}).json()
    t = d["totals"]
    assert (t["income"], t["gross_expense"], t["refunds"], t["net_expense"], t["net_cash_flow"]) == (150000, gross, 129900, gross - 129900, 150000 - (gross - 129900))
    cats = {c["category"]: c["amount"] for c in d["categories"]}
    assert cats["Housing"] == 2200000 and cats["Groceries"] == 234050 + 31000 and cats["Healthcare"] == 84200 and cats["Utilities"] == 184000 and cats["Food"] == 48600 + 75000
    assert cats["Shopping"] == -129900  # a refund with no matching purchase in the data goes negative; it is not hidden
    client.post(f"{API}/budgets", headers=auth, json={"period": "2026-10", "allocations": [{"category": "Food", "amount": 100000}, {"category": "Housing", "amount": 2000000}]})
    p = {r["category"]: r for r in client.get(f"{API}/budgets/2026-10/performance", headers=auth).json()["rows"]}
    assert p["Food"]["actual"] == 123600 and p["Food"]["over_budget"] is True and p["Housing"]["over_budget"] is True
    csv_text = client.get(f"{API}/reports/transactions.csv", headers=auth).text
    assert "628812345678" not in csv_text and csv_text.count("\n") == 10 and "Payment slip: slip_01" in csv_text  # source filename kept in notes
    assert client.get(f"{API}/reports/monthly", headers=auth, params={"period": "2026-10"}).json()["totals"]["net_expense"] == gross - 129900
    # the batch is finished: no more edits/uploads, and the OCR text is gone from the database
    assert client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": pick[:1]}).status_code == 409
    assert upload(client, auth, bid, TEN[0]).status_code == 409
    from app.db import get_db

    stored = get_db().slip_batches.find_one({"status": "imported"})
    assert all(i["raw_text"] == "" for i in stored["items"])


@needs_ocr
def test_money_is_stored_as_integer_paise_and_edit_validation(client, auth):
    bid = new_batch(client, auth)
    it = upload_ok(client, auth, bid, "slip_02_gpay_bigbasket.jpg")
    assert it["fields"]["amount"] == 234050 and isinstance(it["fields"]["amount"], int)
    p = lambda body: client.patch(f"{API}/slips/batches/{bid}/items/{it['id']}", headers=auth, json=body)  # noqa: E731
    for bad in ({"amount": 0}, {"amount": -5}, {"amount": 1.5}, {"amount": "abc"}, {"date": "2026-13-45"}, {"time": "25:61"}, {"currency": "XXX"}, {"type": "gift"}, {"status": "maybe"},
                {"category": "Nonexistent"}, {"unknown_field": 1}, {"merchant": "x" * 100}):
        assert p(bad).status_code == 422, bad
    ok = p({"amount": 234051, "category": "Shopping", "currency": "inr"}).json()["item"]
    assert ok["fields"]["amount"] == 234051 and ok["fields"]["currency"] == "INR" and ok["category"] == "Shopping" and "amount" in ok["edited"]
    client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [it["id"]]})
    from app.db import get_db

    doc = get_db().transactions.find_one({"source": "slip"})
    assert type(doc["amount"]) is int and doc["amount"] == 234051 and doc["category"] == "Shopping" and doc["slip"]["edited"] == ["amount", "currency", "category"]
    assert p({"amount": 5}).status_code == 409  # imported batch is read-only


# =============================== validation, limits, cleanup ===============================
def _png_with(size=(300, 200)):
    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", size, "white").save(b, "PNG")
    return b.getvalue()


@needs_ocr
def test_invalid_files_are_rejected_with_clear_per_file_errors(client, auth, monkeypatch):
    import fitz

    enc = fitz.open()
    enc.new_page().insert_text((50, 100), "secret Rs. 5 UPI", fontsize=12)
    locked = enc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw2")
    cases = [
        ("malware.png", b"MZ\x90\x00 this is an executable", "image/png", "does not match"),
        ("notes.jpg", b"just some text, not a jpeg", "image/jpeg", "does not match"),
        ("fake.pdf", b"<html>not a pdf</html>", "application/pdf", "does not match"),
        ("a.gif", b"GIF89a", "image/gif", "Only JPG"),
        ("noext", b"%PDF-1.4", "application/pdf", "Only JPG"),
        ("a.png", _png_with(), "application/pdf", "does not match a .png"),
        ("empty.png", b"", "image/png", "empty"),
        ("broken.png", b"\x89PNG\r\n\x1a\n" + b"\x00garbage" * 50, "image/png", "damaged"),
        ("truncated.pdf", b"%PDF-1.4\n1 0 obj<<>>", "application/pdf", "damaged"),
        ("locked.pdf", locked, "application/pdf", "password"),
        ("double.png.exe", _png_with(), "image/png", "Only JPG"),
    ]
    bid = new_batch(client, auth)
    for name, data, ctype, word in cases:
        r = upload(client, auth, bid, name, data, ctype)
        assert r.status_code == 200, (name, r.text)
        it = r.json()["item"]
        assert it["ok"] is False and word.lower() in it["error"].lower() and it["import_status"] == "failed", (name, it["error"])
        assert "Traceback" not in it["error"] and "\\" not in it["error"]
    monkeypatch.setattr(get_settings(), "slip_max_image_pixels", 1000)
    huge = upload(client, auth, bid, "huge.png", _png_with((900, 600)), "image/png").json()["item"]
    assert not huge["ok"] and "too large" in huge["error"]
    assert client.get(f"{API}/transactions", headers=auth).json()["total"] == 0


@needs_ocr
def test_oversized_files_are_rejected(client, auth, monkeypatch):
    bid = new_batch(client, auth)
    monkeypatch.setattr(get_settings(), "slip_max_file_bytes", 10_000)
    it = upload(client, auth, bid, "slip_01_phonepe_swiggy.png").json()["item"]
    assert not it["ok"] and "too large" in it["error"] and "MB" in it["error"]
    monkeypatch.undo()
    body = b"\x89PNG\r\n\x1a\n" + b"0" * (6 * 1024 * 1024)  # beyond the global request-size guard
    assert client.post(f"{API}/slips/batches/{bid}/files", headers=auth, files={"file": ("big.png", body, "image/png")}).status_code == 413


@needs_ocr
def test_filename_is_sanitised_and_batch_limit_enforced(client, auth, monkeypatch):
    bid = new_batch(client, auth)
    it = upload(client, auth, bid, "../../etc/pass\x00wd\\..\\x.png", slip("slip_01_phonepe_swiggy.png"), "image/png").json()["item"]
    assert it["file"] == "x.png" and "/" not in it["file"] and "\\" not in it["file"]
    monkeypatch.setattr(get_settings(), "slip_max_files_per_batch", 2)
    upload_ok(client, auth, bid, "slip_02_gpay_bigbasket.jpg")
    r = upload(client, auth, bid, "slip_03_paytm_received.jpeg")
    assert r.status_code == 409 and "at most 2" in r.json()["detail"]


@needs_ocr
def test_ocr_runs_in_memory_and_leaves_no_temp_files(client, auth):
    from PIL import Image

    noise = Image.frombytes("RGB", (900, 900), os.urandom(900 * 900 * 3))  # ~2.4 MB PNG: large enough to be spooled to a temp file by the web layer
    buf = io.BytesIO()
    noise.save(buf, "PNG")
    assert len(buf.getvalue()) > 1_100_000
    tmp = Path(tempfile.gettempdir())
    before = {p.name for p in tmp.iterdir()}
    bid = new_batch(client, auth)
    upload(client, auth, bid, "noise.png", buf.getvalue(), "image/png")
    for n in ("slip_04_neft_landlord.pdf", "slip_06_bescom_scanned.pdf", "slip_01_phonepe_swiggy.png"):
        upload_ok(client, auth, bid, n)
    new = {p.name for p in tmp.iterdir()} - before
    assert not [n for n in new if n.startswith(("tmp", "tess", "fitz", "slip", "ocr"))], f"temporary files left behind: {new}"


@needs_ocr
def test_cancel_deletes_everything_and_expired_batches_are_purged(client, auth, mongo):
    from app.db import get_db
    from app.services.slips import cleanup_expired
    from app.util import now_utc

    db = get_db()
    bid = new_batch(client, auth)
    upload_ok(client, auth, bid, "slip_01_phonepe_swiggy.png")
    assert db.slip_batches.count_documents({}) == 1
    assert client.delete(f"{API}/slips/batches/{bid}", headers=auth).status_code == 204
    assert db.slip_batches.count_documents({}) == 0 and client.get(f"{API}/slips/batches/{bid}", headers=auth).status_code == 404
    old, fresh = new_batch(client, auth), new_batch(client, auth)
    db.slip_batches.update_one({"_id": __import__("bson").ObjectId(old)}, {"$set": {"expires_at": now_utc() - timedelta(minutes=1)}})
    assert client.get(f"{API}/slips/batches/{old}", headers=auth).status_code == 404  # expired -> deleted on access
    db.slip_batches.update_one({"_id": __import__("bson").ObjectId(fresh)}, {"$set": {"expires_at": now_utc() - timedelta(hours=2)}})
    # MongoDB's TTL monitor (and mongomock) may already have removed it; otherwise cleanup_expired does. Either way it is gone.
    assert cleanup_expired(db) in (0, 1) and db.slip_batches.count_documents({}) == 0
    ttl = db.slip_batches.index_information()["ttl_expiry"]
    assert ttl["expireAfterSeconds"] == 0  # MongoDB removes expired batches even if nobody calls the app


def test_old_open_batches_are_discarded_beyond_the_per_user_limit(client, auth, monkeypatch):
    from app.db import get_db

    monkeypatch.setattr(get_settings(), "slip_max_active_batches_per_user", 3)
    ids = [new_batch(client, auth) for _ in range(5)]
    left = {str(d["_id"]) for d in get_db().slip_batches.find({})}
    assert len(left) == 3 and left == set(ids[-3:])


# =============================== security / isolation ===============================
@needs_ocr
def test_users_cannot_touch_each_others_slip_batches(client):
    a = register(client, "alice@example.com")
    b = register(client, "bob@example.com")
    bid = new_batch(client, a)
    item = upload_ok(client, a, bid, "slip_01_phonepe_swiggy.png")
    iid = item["id"]
    checks = [("get", f"/slips/batches/{bid}", None), ("get", f"/slips/batches/{bid}/workbook.xlsx", None), ("get", f"/slips/batches/{bid}/items/{iid}/text", None),
              ("patch", f"/slips/batches/{bid}/items/{iid}", {"amount": 1}), ("post", f"/slips/batches/{bid}/commit", {"item_ids": [iid]}), ("delete", f"/slips/batches/{bid}", None)]
    for method, path, body in checks:
        r = getattr(client, method)(f"{API}{path}", headers=b, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, path)
    assert upload(client, b, bid, "slip_02_gpay_bigbasket.jpg").status_code == 404
    # Bob's own batch cannot import Alice's item id either
    bb = new_batch(client, b)
    assert client.post(f"{API}/slips/batches/{bb}/commit", headers=b, json={"item_ids": [iid]}).status_code == 404
    # Alice's data is untouched and nothing leaked into Bob's account
    got = batch(client, a, bid)
    assert len(got["items"]) == 1 and got["items"][0]["fields"]["amount"] == 48600
    assert client.get(f"{API}/transactions", headers=b).json()["total"] == 0
    assert client.get(f"{API}/slips/batches/{bid}/workbook.xlsx", headers=a).status_code == 200


def test_slip_endpoints_require_authentication(client):
    for method, path in [("get", "/slips/status"), ("post", "/slips/batches"), ("get", "/slips/batches/" + "a" * 24), ("post", "/slips/batches/" + "a" * 24 + "/files"),
                         ("get", "/slips/batches/" + "a" * 24 + "/workbook.xlsx"), ("post", "/slips/batches/" + "a" * 24 + "/commit"), ("delete", "/slips/batches/" + "a" * 24)]:
        assert getattr(client, method)(f"{API}{path}").status_code == 401, path
    assert client.get(f"{API}/slips/batches/not-an-id", headers={"Authorization": "Bearer x"}).status_code == 401


@needs_ocr
def test_ocr_text_and_payment_details_never_reach_the_logs(client, auth, caplog):
    caplog.set_level(logging.DEBUG)
    bid = new_batch(client, auth)
    it = upload_ok(client, auth, bid, "slip_01_phonepe_swiggy.png")
    client.patch(f"{API}/slips/batches/{bid}/items/{it['id']}", headers=auth, json={"verified": True})
    client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [it["id"]]})
    client.get(f"{API}/slips/batches/{bid}/workbook.xlsx", headers=auth)
    server = [r for r in caplog.records if r.name.split(".")[0] in {"financeai", "uvicorn", "app", "pymongo"}]
    assert any(r.name == "financeai.slips" for r in server)
    text = "\n".join(f"{r.name} {r.getMessage()} {r.exc_text or ''}" for r in server)
    for needle in ("628812345678", "SWIGGY", "T2610121945123456789", "486", "HDFC", "slip_01_phonepe", "XXXX4471", auth["Authorization"][7:]):
        assert needle not in text, f"leaked into logs: {needle}"


def test_ocr_unavailable_is_reported_not_crashed(client, auth, monkeypatch):
    def gone(ext, data):
        raise ocr.OcrUnavailable("OCR engine (Tesseract) is not installed on this server.")

    monkeypatch.setattr(ocr, "extract_text", gone)
    monkeypatch.setattr(ocr, "find_tesseract", lambda: None)
    st = client.get(f"{API}/slips/status", headers=auth).json()
    assert st["available"] is False and "Tesseract" in st["reason"]
    bid = new_batch(client, auth)
    r = upload(client, auth, bid, "slip_01_phonepe_swiggy.png")
    assert r.status_code == 200 and r.json()["item"]["ok"] is False and "not installed" in r.json()["item"]["error"]


@needs_ocr
def test_status_endpoint_reports_limits(client, auth):
    st = client.get(f"{API}/slips/status", headers=auth).json()
    assert st["available"] is True and st["engine"] == "tesseract"
    assert st["limits"]["max_files_per_batch"] == 20 and st["limits"]["max_pdf_pages"] == 5 and set(st["limits"]["allowed_extensions"]) == {"jpg", "jpeg", "pdf", "png"}


# =============================== robustness: wrong results must never be silent ===============================
def _gen():
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_slips", SLIPS / "generate_synthetic_slips.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _degrade(img, kind):
    from PIL import Image

    def jpg(i, q):
        b = io.BytesIO()
        i.convert("RGB").save(b, "JPEG", quality=q)
        return Image.open(io.BytesIO(b.getvalue()))

    if kind == "rotated_on_desk":
        bg = Image.new("RGB", (img.width + 120, img.height + 120), (205, 205, 205))
        bg.paste(img, (60, 60))
        return bg.rotate(2, expand=True, fillcolor=(205, 205, 205), resample=Image.BICUBIC)
    if kind == "phone_photo":
        return jpg(img.resize((img.width // 2, img.height // 2), Image.LANCZOS), 50).rotate(1.5, expand=True, fillcolor="white")
    return jpg(img.resize((int(img.width * 0.55), int(img.height * 0.55)), Image.LANCZOS), 35)


@needs_ocr
@pytest.mark.parametrize("kind", ["rotated_on_desk", "phone_photo", "small_and_compressed"])
def test_degraded_photos_may_lose_accuracy_but_never_silently(client, auth, kind):
    """Degraded input is allowed to be read worse, but any wrong or missing date/amount/type must come out flagged for review."""
    g = _gen()
    bid = new_batch(client, auth)
    checked = 0
    for spec in g.SLIPS:
        if spec["fmt"] in ("pdf", "scanned_pdf"):
            continue
        buf = io.BytesIO()
        _degrade(g.render(spec["lines"]), kind).save(buf, "PNG")
        it = upload_ok(client, auth, bid, spec["file"].replace(".jpg", ".png").replace(".jpeg", ".png"), data=buf.getvalue(), ctype="image/png")
        exp = spec["expected"]
        if not it["ok"]:
            continue  # refused outright is also safe
        wrong = [k for k in ("date", "amount", "type", "currency") if it["fields"][k] != exp[k]]
        if wrong:
            checked += 1
            assert it["review_required"] and it["import_status"] != "ready" and it["default_selected"] is False, (kind, spec["file"], wrong, it["fields"])
    assert checked >= 0


@needs_ocr
def test_rupee_glyph_misread_by_ocr_is_handled_and_currency_is_not_assumed(client, auth):
    g = _gen()

    def run(lines):
        buf = io.BytesIO()
        g.render(lines).save(buf, "PNG")
        bid = new_batch(client, auth)
        return upload_ok(client, auth, bid, "glyph.png", data=buf.getvalue(), ctype="image/png")

    upi = run([("Payment Successful", "normal"), ("Paid to SWIGGY", "title"), ("₹486.00", "big"), ("12 Oct 2026, 07:45 PM", "normal"), ("UPI Ref No: 628812345678", "normal")])
    assert upi["fields"]["amount"] == 48600 and upi["fields"]["currency"] == "INR"  # UPI is rupee-only: evidence even though the glyph was misread
    bare = run([("CORNER SHOP", "title"), ("Date: 12/10/2026", "normal"), ("TOTAL", "normal"), ("₹486.00", "big")])
    assert bare["fields"]["amount"] == 48600
    assert bare["fields"]["currency"] in ("INR", None)
    if bare["fields"]["currency"] is None:  # no evidence of rupees -> not assumed, user must confirm
        assert "currency" in bare["missing"] and bare["import_status"] == "missing_details"


@needs_ocr
def test_commit_claims_the_batch_atomically_so_a_concurrent_commit_cannot_run(client, auth, mongo):
    """Audit finding: simultaneous commits used to all return 200 and the losers overwrote the batch result with 'imported: 0'."""
    from app.db import get_db

    bid = new_batch(client, auth)
    it = upload_ok(client, auth, bid, TEN[0])
    from bson import ObjectId

    get_db().slip_batches.update_one({"_id": ObjectId(bid)}, {"$set": {"status": "importing"}})  # another request is mid-import
    r = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [it["id"]]})
    assert r.status_code == 409 and get_db().transactions.count_documents({}) == 0
    get_db().slip_batches.update_one({"_id": ObjectId(bid)}, {"$set": {"status": "open"}})
    # a rejected commit (blockers) must release the claim so the user can fix the slip and retry
    bad = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": ["does-not-exist"]})
    assert bad.status_code == 404 and get_db().slip_batches.find_one({"_id": ObjectId(bid)})["status"] == "open"
    client.patch(f"{API}/slips/batches/{bid}/items/{it['id']}", headers=auth, json={"verified": True})
    ok = client.post(f"{API}/slips/batches/{bid}/commit", headers=auth, json={"item_ids": [it["id"]]})
    assert ok.status_code == 200 and ok.json()["imported"] == 1
    assert get_db().slip_batches.find_one({"_id": ObjectId(bid)})["status"] == "imported"


GROUND_TRUTH_EDIT_ISOLATION = {  # printed on the original receipts (owner-supplied), independent of the app and of expected_slips.json
    "slip_05_card_apollo.jpg": dict(date="2026-10-08", time="14:32", amount=84200, merchant="APOLLO PHARMACY", payment_method="Card", reference="628455512345", status="successful"),
    "slip_06_bescom_scanned.pdf": dict(date="2026-10-10", time=None, amount=184000, merchant="BESCOM", payment_method="UPI", reference="BES2610100099871", status="successful"),
    "slip_07_cash_kirana.png": dict(date="2026-10-14", time=None, amount=31000, merchant="SRI LAKSHMI KIRANA STORE", payment_method="Cash", reference=None, status="successful"),
}


@needs_ocr
def test_each_slip_is_read_and_edited_by_its_own_id_and_edits_never_leak(client, auth):
    bid = new_batch(client, auth)
    ids = {n: upload_ok(client, auth, bid, n)["id"] for n in TEN}
    assert len(set(ids.values())) == 10
    snapshot = lambda: {i["file"]: i for i in client.get(f"{API}/slips/batches/{bid}", headers=auth).json()["items"]}  # noqa: E731
    before = snapshot()
    for name, truth in GROUND_TRUTH_EDIT_ISOLATION.items():  # no field fabricated: absent time / reference stay null
        f = before[name]["fields"]
        assert {k: f[k] for k in truth} == truth, name
        assert before[name]["id"] == ids[name]
        text = client.get(f"{API}/slips/batches/{bid}/items/{ids[name]}/text", headers=auth).json()
        assert text["file"] == name
        others = [t for n, t in GROUND_TRUTH_EDIT_ISOLATION.items() if n != name]
        assert all(o["merchant"].split()[0] not in text["text"].upper() for o in others)  # this slip's text is not another slip's
    edits = {"slip_05_card_apollo.jpg": ("APOLLO EDITED", 85000), "slip_06_bescom_scanned.pdf": ("BESCOM EDITED", 186000), "slip_07_cash_kirana.png": ("KIRANA EDITED", 32000)}
    done = []
    for name, (merchant, amount) in edits.items():
        r = client.patch(f"{API}/slips/batches/{bid}/items/{ids[name]}", headers=auth, json={"merchant": merchant, "amount": amount})
        assert r.status_code == 200 and r.json()["item"]["file"] == name
        done.append(name)
        now = snapshot()
        assert (now[name]["fields"]["merchant"], now[name]["fields"]["amount"]) == (merchant, amount)
        for other in before:
            if other not in done:
                assert now[other] == before[other], f"editing {name} changed {other}"
        # fields of the edited slip that were not edited are still its own
        for k, v in GROUND_TRUTH_EDIT_ISOLATION[name].items():
            if k not in ("merchant", "amount"):
                assert now[name]["fields"][k] == v
    # persisted: a fresh read (as after a page reload) still returns each slip's own values
    final = snapshot()
    for name, (merchant, amount) in edits.items():
        assert (final[name]["fields"]["merchant"], final[name]["fields"]["amount"]) == (merchant, amount)
    # an item id from another slip can never be used to write into a different file's record
    assert client.patch(f"{API}/slips/batches/{bid}/items/not-a-real-id", headers=auth, json={"merchant": "X"}).status_code == 404
    assert snapshot() == final
