import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-123456")
os.environ.setdefault("AUTH_RATE_LIMIT_PER_MINUTE", "1000")

import mongomock  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db as database  # noqa: E402
from app.main import app  # noqa: E402
from app.security import auth_limiter  # noqa: E402

API = "/api/v1"


@pytest.fixture()
def mongo():
    """In-memory mongomock by default. Set TEST_MONGODB_URI to run the same suite against a real MongoDB.

    With a real server, each test uses its own uniquely named throwaway database that is dropped afterwards.
    """
    uri = os.environ.get("TEST_MONGODB_URI")
    if not uri:
        client = mongomock.MongoClient(tz_aware=True)
        database.set_client_for_tests(client)
        yield client
        database.set_client_for_tests(None)
        return
    import uuid

    from pymongo import MongoClient

    from app.config import get_settings

    settings = get_settings()
    original = settings.mongodb_db
    name = f"financeai_pytest_{uuid.uuid4().hex[:12]}"
    settings.mongodb_db = name
    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    database.set_client_for_tests(client)
    try:
        yield client
    finally:
        client.drop_database(name)  # only this test's own database
        client.close()
        settings.mongodb_db = original
        database.set_client_for_tests(None)


@pytest.fixture()
def client(mongo):
    auth_limiter.reset()
    return TestClient(app, raise_server_exceptions=False)


def register(client, email="alice@example.com", password="Passw0rd!x", name="Alice"):
    r = client.post(f"{API}/auth/register", json={"email": email, "password": password, "name": name})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture()
def auth(client):
    return register(client)


def csv_bytes(rows, header="Date,Description,Debit,Credit"):
    return (header + "\n" + "\n".join(rows) + "\n").encode()


def make_statement(months, start="2025-01"):
    """Deterministic statement: salary 100,000 + rent 30,000 + Netflix 649 + groceries/food, for N months."""
    y, m = map(int, start.split("-"))
    rows = []
    for i in range(months):
        yy, mm = y + (m - 1 + i) // 12, (m - 1 + i) % 12 + 1
        p = f"{yy}-{mm:02d}"
        rows += [
            f"{p}-01,Salary ACME Corp,,100000.00",
            f"{p}-02,House Rent Landlord,30000.00,",
            f"{p}-05,Netflix Subscription,649.00,",
            f"{p}-08,BigBasket Groceries,{4000 + 100 * i}.00,",
            f"{p}-12,Swiggy Order,{1500 + 50 * i}.00,",
            f"{p}-20,Uber Ride,{800}.00,",
            f"{p}-25,PVR Cinemas,{1200}.00,",
        ]
    return rows


def import_statement(client, auth, rows, filename="stmt.csv", header="Date,Description,Debit,Credit"):
    r = client.post(f"{API}/imports/preview", headers=auth, files={"file": (filename, csv_bytes(rows, header), "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    c = client.post(f"{API}/imports/{body['import_id']}/commit", headers=auth, json={})
    assert c.status_code == 200, c.text
    return body, c.json()


def recent_start(n_months):
    """Period string of the first of n completed months ending last month (relative to today)."""
    from datetime import datetime, timezone

    from app.util import add_months, month_key

    return add_months(month_key(datetime.now(timezone.utc).date()), -n_months)
