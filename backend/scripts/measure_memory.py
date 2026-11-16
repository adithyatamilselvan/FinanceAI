"""Measure resident memory (RSS) of the API process across realistic workloads.

Usage (from backend/):  python scripts/measure_memory.py [mongodb-uri]
Uses a throwaway database on the given MongoDB (default mongodb://localhost:27017) and drops it afterwards.
Everything runs in one process via the ASGI test client, so RSS reflects the app + libraries + workload.
"""
import gc
import io
import os
import sys
import uuid

os.environ["ENVIRONMENT"] = "test"
os.environ["JWT_SECRET"] = "memory-test-secret-memory-test-secret-123"
os.environ["AUTH_RATE_LIMIT_PER_MINUTE"] = "100000"
os.environ["MAX_IMPORT_ROWS"] = "5000"

import psutil  # noqa: E402

proc = psutil.Process()
peak = 0.0
log: list[tuple[str, float]] = []


def rss() -> float:
    return proc.memory_info().rss / 1048576


def mark(label: str) -> None:
    global peak
    gc.collect()
    v = rss()
    peak = max(peak, v)
    log.append((label, v))
    print(f"{label:<52s} {v:7.1f} MB", flush=True)


mark("python + psutil baseline")
uri = sys.argv[1] if len(sys.argv) > 1 else "mongodb://localhost:27017"
from pymongo import MongoClient  # noqa: E402

from app import db as database  # noqa: E402
from app.config import get_settings  # noqa: E402

name = f"financeai_mem_{uuid.uuid4().hex[:10]}"
get_settings().mongodb_db = name
from app.main import app  # noqa: E402

mark("after importing FastAPI app (startup footprint)")
from fastapi.testclient import TestClient  # noqa: E402

client_db = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
database.set_client_for_tests(client_db)
c = TestClient(app)
API = "/api/v1"
try:
    assert c.get("/health").status_code == 200
    mark("after /health")
    assert c.get("/ready").status_code == 200
    mark("after DB connection (/ready)")
    r = c.post(f"{API}/auth/register", json={"email": "m@example.com", "password": "Passw0rd!x", "name": "Mem"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    mark("after register (argon2 hash)")

    from datetime import datetime, timezone

    from app.util import add_months, month_key

    cur = month_key(datetime.now(timezone.utc).date())
    recent = [add_months(cur, -k) for k in range(12, 0, -1)]  # last 12 completed months
    kinds = ["Swiggy order", "BigBasket groceries", "Uber ride", "PVR cinema", "Amazon purchase", "Apollo pharmacy", "Electricity bill", "Corner shop"]

    def desc(i, tag):
        return f"{kinds[i % len(kinds)]} {tag}{i % 60} ref{i}"

    def csv_rows(n, tag="a"):
        out = ["Date,Description,Debit,Credit"]
        for i in range(n):
            p, d = recent[i % 12], 1 + i % 28
            if i % 40 == 0:
                out.append(f"{p}-{d:02d},Salary ACME {i},,{50000 + i}.00")
            else:
                out.append(f"{p}-{d:02d},{desc(i, tag)},{10 + i % 900}.{i % 100:02d},")
        return "\n".join(out).encode()

    def do_import(content, name_):
        p = c.post(f"{API}/imports/preview", headers=h, files={"file": (name_, content)})
        assert p.status_code == 200, p.text
        j = p.json()
        cm = c.post(f"{API}/imports/{j['import_id']}/commit", headers=h, json={})
        assert cm.status_code == 200, cm.text
        return j["summary"]["valid_rows"], cm.json()["inserted"]

    print("CSV import of 5000 rows:", do_import(csv_rows(5000), "a.csv"))
    mark("after CSV import (5000 rows, preview+commit)")

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Date", "Description", "Amount"])
    for i in range(5000):
        ws.append([f"{recent[i % 12]}-{1 + i % 28:02d}", desc(i, "x"), -(10 + i % 700) - 0.25])
    buf = io.BytesIO()
    wb.save(buf)
    del wb, ws
    print("XLSX import of 5000 rows:", do_import(buf.getvalue(), "b.xlsx"))
    del buf
    mark("after XLSX import (5000 rows, preview+commit)")

    for label, path in [("monthly analytics (dashboard)", "/analytics/dashboard"), ("category trends", "/analytics/category-trends"),
                        ("anomaly detection", "/analytics/anomalies"), ("forecast", "/forecasts")]:
        assert c.get(f"{API}{path}", headers=h).status_code == 200, path
        mark(f"after {label}")
    assert c.post(f"{API}/recurring/detect", headers=h).status_code == 200
    mark("after recurring detection")
    o = c.post(f"{API}/optimization/run", headers=h, json={"months": 6})
    print("optimization status:", o.status_code)
    mark("after budget optimisation (SciPy)")
    s = c.post(f"{API}/simulations/run", headers=h, json={"horizon_months": 360, "base_monthly_income": 10000000, "base_monthly_expense": 6000000,
                                                          "annual_return_pct": 8, "loan": {"principal": 100000000, "annual_rate_pct": 9, "tenure_months": 240, "extra_monthly_payment": 100000}})
    print("simulation status:", s.status_code)
    mark("after what-if simulation (360 months + loan)")
    pdf = c.get(f"{API}/reports/monthly.pdf", headers=h)
    print("pdf bytes:", len(pdf.content))
    del pdf
    mark("after PDF report")
    csv_exp = c.get(f"{API}/reports/transactions.csv", headers=h)
    print("csv export bytes:", len(csv_exp.content))
    del csv_exp
    mark("after CSV export (10000 rows)")
    xl = c.get(f"{API}/reports/transactions.xlsx", headers=h)
    print("xlsx export bytes:", len(xl.content))
    del xl
    mark("after Excel export (10000 rows)")

    for rnd in (1, 2, 3):
        before = rss()
        for _ in range(15):
            c.get(f"{API}/analytics/dashboard", headers=h)
            c.get(f"{API}/forecasts", headers=h)
            c.post(f"{API}/optimization/run", headers=h, json={"months": 6})
            c.get(f"{API}/reports/monthly.pdf", headers=h)
            c.get(f"{API}/transactions", headers=h, params={"page_size": 100})
        mark(f"after 15 repeated request rounds, pass {rnd} (leak check)")
        print(f"   growth in pass {rnd}: {rss() - before:+.1f} MB  (pass 1 includes one-off lazy module loads; a leak would keep growing)")
    # leak check on imports: re-upload the same 5000-row file 5 times (all duplicates)
    before = rss()
    content = csv_rows(5000)
    for _ in range(5):
        p = c.post(f"{API}/imports/preview", headers=h, files={"file": ("a.csv", content)})
        c.delete(f"{API}/imports/{p.json()['import_id']}", headers=h)
    mark("after 5 duplicate 5000-row previews (leak check)")
    print(f"growth over 5 duplicate previews: {rss() - before:+.1f} MB")
    print(f"\nPEAK RSS observed: {peak:.1f} MB (Cloud Run limit configured: 2048 MB; Render's old limit: 512 MB)")
finally:
    client_db.drop_database(name)
    client_db.close()
