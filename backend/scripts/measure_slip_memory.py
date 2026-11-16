"""Measure memory/time of the Payment Slip Scanner (Python process + the Tesseract child process) on the synthetic slips.

Usage (from backend/):  python scripts/measure_slip_memory.py [mongodb-uri]
Samples the combined RSS of this process and all child processes every 20 ms while a 10-slip batch (and a larger scanned page) is processed.
"""
import gc
import io
import os
import sys
import threading
import time
import uuid
from pathlib import Path

os.environ.update(ENVIRONMENT="test", JWT_SECRET="memory-test-secret-memory-test-secret-123", AUTH_RATE_LIMIT_PER_MINUTE="100000")
import psutil  # noqa: E402

proc = psutil.Process()
peak_total = [0.0]
stop = threading.Event()


def sampler():
    while not stop.is_set():
        try:
            tot = proc.memory_info().rss + sum(c.memory_info().rss for c in proc.children(recursive=True))
            peak_total[0] = max(peak_total[0], tot / 1048576)
        except psutil.Error:
            pass
        time.sleep(0.02)


def rss():
    gc.collect()
    return proc.memory_info().rss / 1048576


from pymongo import MongoClient  # noqa: E402

from app import db as database  # noqa: E402
from app.config import get_settings  # noqa: E402

name = f"financeai_slipmem_{uuid.uuid4().hex[:8]}"
get_settings().mongodb_db = name
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

DIR = Path(__file__).resolve().parents[2] / "docs" / "test-data" / "payment-slips"
uri = sys.argv[1] if len(sys.argv) > 1 else "mongodb://localhost:27017"
mc = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
database.set_client_for_tests(mc)
c = TestClient(app)
API = "/api/v1"
try:
    print(f"app imported, before any OCR:            {rss():7.1f} MB")
    h = {"Authorization": "Bearer " + c.post(f"{API}/auth/register", json={"email": "m@example.com", "password": "Passw0rd!x", "name": "M"}).json()["access_token"]}
    t = threading.Thread(target=sampler, daemon=True)
    t.start()
    bid = c.post(f"{API}/slips/batches", headers=h).json()["id"]
    files = sorted(p for p in DIR.glob("slip_*"))
    t0 = time.time()
    per = []
    for f in files:
        s = time.time()
        r = c.post(f"{API}/slips/batches/{bid}/files", headers=h, files={"file": (f.name, f.read_bytes())})
        assert r.status_code == 200, r.text
        per.append(time.time() - s)
    print(f"10-slip batch: {time.time() - t0:5.1f} s total, {max(per):.2f} s slowest file, {sum(per) / len(per):.2f} s average")
    print(f"after the batch (Python process only):   {rss():7.1f} MB")
    print(f"PEAK python + tesseract during batch:    {peak_total[0]:7.1f} MB")

    # a full-size scanned A4 page (rendered at 200 dpi inside the app) and a 5-page scanned PDF = the worst allowed PDF
    import fitz
    from PIL import Image, ImageDraw, ImageFont

    def a4_scan(i):
        img = Image.new("RGB", (1654, 2339), "white")  # A4 @ 200 dpi
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 34)
        except OSError:
            font = ImageFont.load_default(size=34)
        for k in range(45):
            d.text((120, 120 + k * 46), f"Line {k} Paid to SHOP {i} Rs. {100 + k}.00 UTR: 62881234{i}{k:04d}", fill="black", font=font)
        b = io.BytesIO()
        img.save(b, "JPEG", quality=60)  # like a real scan: compressed, so the PDF stays under the 5 MB upload limit
        return b.getvalue()

    doc = fitz.open()
    for i in range(5):
        page = doc.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=a4_scan(i))
    pdf = doc.tobytes()
    peak_total[0] = 0
    s = time.time()
    r = c.post(f"{API}/slips/batches/{bid}/files", headers=h, files={"file": ("five_page_scan.pdf", pdf)})
    body = r.json()
    item = body.get("item", {})
    print(f"5-page scanned A4 PDF ({len(pdf) / 1048576:.1f} MB): {time.time() - s:5.1f} s, HTTP {r.status_code}, ok={item.get('ok')}, pages={item.get('pages')}, error={item.get('error') or body.get('detail')}")
    print(f"PEAK python + tesseract during that PDF: {peak_total[0]:7.1f} MB")
    workbook = c.get(f"{API}/slips/batches/{bid}/workbook.xlsx", headers=h)
    print(f"workbook: {len(workbook.content) / 1024:.0f} KB; python RSS now {rss():.1f} MB")
    stop.set()
finally:
    mc.drop_database(name)
    mc.close()
