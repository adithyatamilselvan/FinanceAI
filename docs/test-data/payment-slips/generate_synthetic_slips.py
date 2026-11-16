"""Generate SYNTHETIC payment slips for testing the Payment Slip Scanner. All names, numbers and amounts are fictional.

Writes next to this script:
  slip_01_... slip_10_...   ten slips (PNG / JPG / JPEG / digital PDF / scanned PDF) used for the 10-file acceptance test
  edge-cases/*              missing date, missing amount, blurry/noisy, blank, not-a-slip
  expected_slips.json       ground truth of what a correct extraction must contain

Run:  python generate_synthetic_slips.py          (needs Pillow and reportlab, both already backend dependencies)
"""
import io
import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).parent
FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf",
]
BOLD_CANDIDATES = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
SIZES = {"title": 46, "big": 64, "normal": 32, "small": 26}


def _font(size: int, bold: bool = False):
    for p in (BOLD_CANDIDATES if bold else []) + FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def render(lines: list[tuple[str, str]], width: int = 900) -> Image.Image:
    """lines = [(text, 'title'|'big'|'normal'|'small'|'gap')]. White card on grey, like a screenshot or a printed slip."""
    height = 80 + sum(SIZES.get(k, 20) + 22 if k != "gap" else 30 for _, k in lines)
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    y = 40
    for text, kind in lines:
        if kind == "gap":
            y += 30
            continue
        size = SIZES[kind]
        d.text((50, y), text, fill="black", font=_font(size, bold=kind in ("title", "big")))
        y += size + 22
    return img


# ---------------------------------------------------------------- the 10 acceptance slips
# expected: what a correct extraction contains (None = not visible on the slip, must stay None)
SLIPS = [
    dict(file="slip_01_phonepe_swiggy.png", fmt="png", lines=[("PhonePe", "title"), ("Payment Successful", "normal"), ("Paid to", "small"), ("SWIGGY", "title"), ("Rs. 486.00", "big"),
         ("12 Oct 2026, 07:45 PM", "normal"), ("Transaction ID", "small"), ("T2610121945123456789", "normal"), ("UTR: 628812345678", "normal"), ("Debited from XXXX4471 HDFC Bank", "small"), ("Powered by UPI", "small")],
         expected=dict(date="2026-10-12", time="19:45", amount=48600, currency="INR", type="expense", merchant="SWIGGY", sender=None, payment_method="UPI", reference="628812345678", provider="PhonePe", status="successful")),
    dict(file="slip_02_gpay_bigbasket.jpg", fmt="jpg", lines=[("Google Pay", "title"), ("Payment completed", "normal"), ("To BIGBASKET", "title"), ("Rs. 2,340.50", "big"), ("Oct 15, 2026 at 6:12 PM", "normal"),
         ("UPI transaction ID", "small"), ("529814327654", "normal"), ("From: HDFC Bank 4471", "small")],
         expected=dict(date="2026-10-15", time="18:12", amount=234050, currency="INR", type="expense", merchant="BIGBASKET", sender=None, payment_method="UPI", reference="529814327654", provider="Google Pay", status="successful")),
    dict(file="slip_03_paytm_received.jpeg", fmt="jpeg", lines=[("Paytm", "title"), ("Money Received", "normal"), ("From Rahul Verma", "title"), ("Rs. 1,500.00", "big"), ("Oct 18, 2026, 11:20 AM", "normal"),
         ("Credited to SBI A/c XX5521", "small"), ("UPI Ref No: 530198877665", "normal"), ("Order ID 20261018112000", "small")],
         expected=dict(date="2026-10-18", time="11:20", amount=150000, currency="INR", type="income", merchant=None, sender="Rahul Verma", payment_method="UPI", reference="530198877665", provider="Paytm", status="successful")),
    dict(file="slip_04_neft_landlord.pdf", fmt="pdf", lines=[("ICICI Bank NetBanking", "title"), ("Fund Transfer Successful", "normal"), ("Transfer Mode: NEFT", "normal"), ("Beneficiary Name: LANDLORD R KUMAR", "normal"),
         ("Amount: INR 22,000.00", "normal"), ("Date: 03-10-2026", "normal"), ("Time: 10:05:33", "normal"), ("UTR Number: ICIC26100312345678", "normal"), ("Remarks: Rent October", "normal"), ("Debit Account: XXXX4471", "small")],
         expected=dict(date="2026-10-03", time="10:05", amount=2200000, currency="INR", type="expense", merchant="LANDLORD R KUMAR", sender=None, payment_method="Bank transfer", reference="ICIC26100312345678", provider="ICICI Bank", status="successful")),
    dict(file="slip_05_card_apollo.jpg", fmt="jpg", lines=[("APOLLO PHARMACY", "title"), ("Store 3321, Bengaluru", "small"), ("CARD SALE RECEIPT", "normal"), ("DATE: 08/10/2026   TIME: 14:32", "normal"), ("CARD: VISA **** 4471", "normal"),
         ("APPROVAL CODE: 884512", "normal"), ("RRN: 628455512345", "normal"), ("AMOUNT: INR 842.00", "normal"), ("STATUS: APPROVED", "normal")],
         expected=dict(date="2026-10-08", time="14:32", amount=84200, currency="INR", type="expense", merchant="APOLLO PHARMACY", sender=None, payment_method="Card", reference="628455512345", provider=None, status="successful")),
    dict(file="slip_06_bescom_scanned.pdf", fmt="scanned_pdf", lines=[("BESCOM Online Payment Receipt", "title"), ("Bill Payment Successful", "normal"), ("Biller Name: BESCOM", "normal"), ("Consumer No: 1234567890", "normal"),
         ("Amount Paid: Rs. 1,840.00", "normal"), ("Payment Date: 10/10/2026", "normal"), ("Transaction Ref: BES2610100099871", "normal"), ("Payment Mode: UPI", "normal")],
         expected=dict(date="2026-10-10", time=None, amount=184000, currency="INR", type="expense", merchant="BESCOM", sender=None, payment_method="UPI", reference="BES2610100099871", provider=None, status="successful")),
    dict(file="slip_07_cash_kirana.png", fmt="png", lines=[("SRI LAKSHMI KIRANA STORE", "title"), ("CASH RECEIPT", "normal"), ("Date: 14-10-2026", "normal"), ("Rice 5kg, Dal 2kg, Oil 1L", "small"), ("TOTAL: Rs. 310.00", "normal"), ("Paid in cash", "normal"), ("Thank you, visit again", "small")],
         expected=dict(date="2026-10-14", time=None, amount=31000, currency="INR", type="expense", merchant="SRI LAKSHMI KIRANA STORE", sender=None, payment_method="Cash", reference=None, provider=None, status="successful")),
    dict(file="slip_08_failed_actfibernet.png", fmt="png", lines=[("PhonePe", "title"), ("Payment Failed", "title"), ("To ACT FIBERNET", "normal"), ("Rs. 999.00", "big"), ("16 Oct 2026, 09:10 AM", "normal"),
         ("Transaction ID T2610160910999888777", "small"), ("If money was debited it will be refunded", "small"), ("within 3-5 working days", "small")],
         expected=dict(date="2026-10-16", time="09:10", amount=99900, currency="INR", type="expense", merchant="ACT FIBERNET", sender=None, payment_method="UPI", reference="T2610160910999888777", provider="PhonePe", status="failed")),
    dict(file="slip_09_pending_zomato.jpg", fmt="jpg", lines=[("Paytm", "title"), ("Payment Pending", "title"), ("Paid to ZOMATO", "normal"), ("Rs. 750.00", "big"), ("20 Oct 2026, 08:25 PM", "normal"),
         ("Your payment is being processed", "small"), ("Order ID 20261020082598", "small")],
         expected=dict(date="2026-10-20", time="20:25", amount=75000, currency="INR", type="expense", merchant="ZOMATO", sender=None, payment_method="UPI", reference="20261020082598", provider="Paytm", status="pending")),
    dict(file="slip_10_refund_amazon.png", fmt="png", lines=[("Refund Successful", "title"), ("Refund from AMAZON", "normal"), ("Rs. 1,299.00", "big"), ("22 Oct 2026, 03:40 PM", "normal"),
         ("Refund ID RF2610221540123", "normal"), ("Credited to HDFC Bank XXXX4471", "small")],
         expected=dict(date="2026-10-22", time="15:40", amount=129900, currency="INR", type="refund", merchant="AMAZON", sender=None, payment_method=None, reference="RF2610221540123", provider="HDFC Bank", status="refunded")),
]

# ---------------------------------------------------------------- edge cases (not part of the 10)
EDGE = [
    dict(file="edge-cases/edge_missing_date.png", fmt="png", lines=[("PhonePe", "title"), ("Payment Successful", "normal"), ("Paid to", "small"), ("CAFE MOCHA", "title"), ("Rs. 220.00", "big"), ("UTR: 628812340001", "normal")],
         expected=dict(date=None, amount=22000, status="successful")),
    dict(file="edge-cases/edge_missing_amount.png", fmt="png", lines=[("PhonePe", "title"), ("Payment Successful", "normal"), ("Paid to", "small"), ("CAFE MOCHA", "title"), ("12 Oct 2026, 07:45 PM", "normal"), ("UTR: 628812340002", "normal")],
         expected=dict(date="2026-10-12", amount=None, status="successful")),
    dict(file="edge-cases/edge_blurry_noisy.jpg", fmt="jpg", noisy=True, lines=[("Google Pay", "title"), ("Payment completed", "normal"), ("To METRO CASH", "title"), ("Rs. 3,150.75", "big"), ("Oct 19, 2026 at 5:05 PM", "normal"), ("UPI transaction ID", "small"), ("529814320003", "normal")],
         expected=dict(date="2026-10-19", amount=315075, status="successful", note="degraded on purpose: must be flagged for review, never silently trusted")),
    dict(file="edge-cases/edge_blank.png", fmt="png", lines=[], expected=dict(note="no text at all")),
    dict(file="edge-cases/edge_not_a_slip.png", fmt="png", lines=[("Weekend recipe notes", "title"), ("Boil the rice for twenty minutes", "normal"), ("Add salt and stir well", "normal"), ("Serve warm", "normal")], expected=dict(note="text but no transaction")),
]


def degrade(img: Image.Image) -> Image.Image:
    rnd = random.Random(7)
    img = img.resize((img.width // 2, img.height // 2)).filter(ImageFilter.GaussianBlur(1.6))
    px = img.load()
    for _ in range(img.width * img.height // 6):
        x, y = rnd.randrange(img.width), rnd.randrange(img.height)
        v = rnd.randrange(60, 200)
        px[x, y] = (v, v, v)
    return img.rotate(3, expand=True, fillcolor="white")


def to_bytes(spec: dict) -> bytes:
    fmt = spec["fmt"]
    if fmt == "pdf":  # digital PDF: real text objects (no OCR needed)
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        y = 780
        for text, kind in spec["lines"]:
            if kind == "gap":
                y -= 20
                continue
            c.setFont("Helvetica-Bold" if kind in ("title", "big") else "Helvetica", {"title": 16, "big": 20, "normal": 12, "small": 10}[kind])
            c.drawString(60, y, text)
            y -= 26
        c.save()
        return buf.getvalue()
    img = render(spec["lines"]) if spec["lines"] else Image.new("RGB", (900, 600), "white")
    if spec.get("noisy"):
        img = degrade(img)
    buf = io.BytesIO()
    if fmt == "scanned_pdf":  # image-only PDF: must be OCRed
        img.save(buf, "PDF", resolution=150)
    elif fmt in ("jpg", "jpeg"):
        img.save(buf, "JPEG", quality=88)
    else:
        img.save(buf, "PNG")
    return buf.getvalue()


def main():
    (OUT / "edge-cases").mkdir(exist_ok=True)
    truth = {}
    for spec in SLIPS + EDGE:
        (OUT / spec["file"]).write_bytes(to_bytes(spec))
        truth[spec["file"]] = spec["expected"]
    (OUT / "expected_slips.json").write_text(json.dumps(truth, indent=2), encoding="utf-8")
    print(f"wrote {len(SLIPS)} slips + {len(EDGE)} edge cases")


if __name__ == "__main__":
    main()
