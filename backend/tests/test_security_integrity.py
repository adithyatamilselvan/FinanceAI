"""Phase 5: authorisation, injection, credential storage, upload hardening and log hygiene."""
import io
import json
import logging
import os
import time
import zipfile

import jwt
import pytest

from app.config import get_settings
from tests.conftest import API, csv_bytes, import_statement, make_statement, recent_start, register

PASSWORD = "Passw0rd!x"


def two_users(client):
    a = register(client, "alice@example.com", PASSWORD)
    b = register(client, "bob@example.com", PASSWORD)
    return a, b


def seed_everything(client, h):
    """Create one record of every owned resource type for user h and return their ids."""
    start = recent_start(4)
    import_statement(client, h, make_statement(4, start))
    ids = {}
    ids["tx"] = client.get(f"{API}/transactions", headers=h).json()["items"][0]["id"]
    ids["goal"] = client.post(f"{API}/goals", headers=h, json={"name": "G", "target_amount": 100000}).json()["id"]
    client.post(f"{API}/budgets", headers=h, json={"period": "2026-01", "allocations": [{"category": "Food", "amount": 1000}]})
    client.post(f"{API}/recurring/detect", headers=h)
    ids["recurring"] = client.get(f"{API}/recurring", headers=h).json()["items"][0]["id"]
    ids["rec"] = client.post(f"{API}/optimization/run", headers=h, json={"months": 3}).json()["id"]
    ids["sim"] = client.post(f"{API}/simulations/run", headers=h, json={"base_monthly_income": 100000, "base_monthly_expense": 50000, "save": True}).json()["id"]
    client.post(f"{API}/categories", headers=h, json={"name": "Pets"})
    t = client.post(f"{API}/transactions", headers=h, json={"date": "2026-01-01", "description": "Quirky Bistro 9", "amount": 100, "type": "expense"}).json()
    client.patch(f"{API}/transactions/{t['id']}", headers=h, json={"category": "Food"})
    ids["rule"] = client.get(f"{API}/categories/rules", headers=h).json()["rules"][0]["id"]
    p = client.post(f"{API}/imports/preview", headers=h, files={"file": ("x.csv", csv_bytes(["2026-11-01,Staged,1.00,"]))}).json()
    ids["staged_import"] = p["import_id"]
    return ids


def test_no_cross_user_access_to_any_resource_by_id(client):
    a, b = two_users(client)
    ids = seed_everything(client, a)
    attempts = [
        ("get", f"/transactions/{ids['tx']}", None), ("patch", f"/transactions/{ids['tx']}", {"notes": "x"}), ("delete", f"/transactions/{ids['tx']}", None),
        ("get", f"/goals/{ids['goal']}", None), ("patch", f"/goals/{ids['goal']}", {"name": "x"}), ("delete", f"/goals/{ids['goal']}", None),
        ("post", f"/goals/{ids['goal']}/contributions", {"amount": 5}),
        ("patch", f"/recurring/{ids['recurring']}", {"status": "dismissed"}),
        ("get", f"/optimization/recommendations/{ids['rec']}", None), ("post", f"/optimization/recommendations/{ids['rec']}/accept", {"scenario": "balanced"}),
        ("post", f"/optimization/recommendations/{ids['rec']}/reject", None), ("delete", f"/simulations/{ids['sim']}", None),
        ("delete", f"/categories/rules/{ids['rule']}", None),
        ("get", f"/imports/{ids['staged_import']}/rows", None), ("post", f"/imports/{ids['staged_import']}/commit", {}), ("delete", f"/imports/{ids['staged_import']}", None),
    ]
    for method, path, body in attempts:
        r = getattr(client, method)(f"{API}{path}", headers=b, **({"json": body} if body is not None else {}))
        assert r.status_code == 404, (method, path, r.status_code)
    # Bob sees nothing of Alice's data anywhere, and Alice's data is untouched by the attempts
    for path in ("/transactions", "/goals", "/recurring", "/simulations", "/budgets", "/categories/rules", "/imports", "/optimization/recommendations"):
        body = client.get(f"{API}{path}", headers=b).json()
        assert (body.get("total") == 0) if "total" in body else all(not v for k, v in body.items() if isinstance(v, list)), path
    assert client.get(f"{API}/budgets/2026-01", headers=b).status_code == 404 and client.get(f"{API}/categories", headers=b).json()["categories"] != []
    assert not any(c["name"] == "Pets" for c in client.get(f"{API}/categories", headers=b).json()["categories"])
    assert client.get(f"{API}/transactions/{ids['tx']}", headers=a).status_code == 200
    assert client.get(f"{API}/goals/{ids['goal']}", headers=a).json()["current_amount"] == 0
    assert client.get(f"{API}/imports/{ids['staged_import']}/rows", headers=a).status_code == 200  # staged import still intact for owner
    assert client.get(f"{API}/analytics/dashboard", headers=b).json()["totals"]["income"] == 0
    assert client.get(f"{API}/reports/transactions.csv", headers=b).text.strip().count("\n") == 0


@pytest.mark.parametrize("path", ["/profile", "/transactions", "/imports", "/budgets", "/goals", "/goals/emergency-fund", "/recurring", "/analytics/dashboard", "/analytics/anomalies",
                                  "/analytics/health-score", "/forecasts", "/optimization/recommendations", "/simulations", "/reports/monthly", "/reports/monthly.pdf",
                                  "/reports/transactions.csv", "/reports/transactions.xlsx", "/categories"])
def test_every_protected_get_requires_auth(client, path):
    assert client.get(f"{API}{path}").status_code == 401


@pytest.mark.parametrize("method,path", [("post", "/transactions"), ("post", "/imports/preview"), ("post", "/budgets"), ("post", "/goals"), ("post", "/recurring/detect"),
                                         ("post", "/optimization/run"), ("post", "/simulations/run"), ("post", "/auth/logout"), ("patch", "/profile"), ("post", "/categories")])
def test_every_protected_write_requires_auth(client, method, path):
    assert getattr(client, method)(f"{API}{path}", json={}).status_code == 401


def test_token_attacks(client):
    s = get_settings()
    h = register(client)
    uid = client.get(f"{API}/profile", headers=h).json()["id"]
    now = int(time.time())
    none_alg = jwt.encode({"sub": uid, "tv": 0, "exp": now + 99}, key="", algorithm="none")
    assert client.get(f"{API}/profile", headers={"Authorization": f"Bearer {none_alg}"}).status_code == 401
    hs512 = jwt.encode({"sub": uid, "tv": 0, "exp": now + 99}, s.jwt_secret, algorithm="HS512")
    assert client.get(f"{API}/profile", headers={"Authorization": f"Bearer {hs512}"}).status_code == 401
    no_exp = jwt.encode({"sub": uid, "tv": 0}, s.jwt_secret, algorithm="HS256")
    assert client.get(f"{API}/profile", headers={"Authorization": f"Bearer {no_exp}"}).status_code == 401
    ghost = jwt.encode({"sub": "6" * 24, "tv": 0, "exp": now + 99}, s.jwt_secret, algorithm="HS256")
    assert client.get(f"{API}/profile", headers={"Authorization": f"Bearer {ghost}"}).status_code == 401
    assert client.get(f"{API}/profile", headers={"Authorization": f"Basic {h['Authorization'][7:]}"}).status_code == 401
    assert client.get(f"{API}/profile", headers={"Authorization": "Bearer"}).status_code == 401
    inj = jwt.encode({"sub": {"$ne": None}, "tv": 0, "exp": now + 99}, s.jwt_secret, algorithm="HS256")
    assert client.get(f"{API}/profile", headers={"Authorization": f"Bearer {inj}"}).status_code == 401


def test_password_change_and_logout_revoke_existing_tokens(client):
    h = register(client)
    r = client.post(f"{API}/profile/password", headers=h, json={"current_password": "wrong-pass1", "new_password": "NewPassw0rd!"})
    assert r.status_code == 400 and client.get(f"{API}/profile", headers=h).status_code == 200
    assert client.post(f"{API}/profile/password", headers=h, json={"current_password": PASSWORD, "new_password": "NewPassw0rd!"}).status_code == 204
    assert client.get(f"{API}/profile", headers=h).status_code == 401  # old token dead
    assert client.post(f"{API}/auth/login", json={"email": "alice@example.com", "password": PASSWORD}).status_code == 401
    assert client.post(f"{API}/auth/login", json={"email": "alice@example.com", "password": "NewPassw0rd!"}).status_code == 200


def test_nosql_and_mass_assignment_payloads_are_rejected(client):
    h = register(client)
    for payload in ({"email": {"$ne": None}, "password": {"$ne": None}}, {"email": "a@example.com", "password": {"$gt": ""}}, {"email": ["a@example.com"], "password": "x"}):
        assert client.post(f"{API}/auth/login", json=payload).status_code == 422
    assert client.get(f"{API}/transactions", headers=h, params={"category": '{"$ne": ""}', "q": ".*", "type": "$ne"}).status_code == 422
    assert client.get(f"{API}/transactions/%7B%22%24gt%22%3A%22%22%7D", headers=h).status_code == 404
    for body in ({"token_version": 99}, {"password_hash": "x"}, {"_id": "x"}, {"email": "evil@example.com"}, {"is_admin": True}):
        assert client.patch(f"{API}/profile", headers=h, json=body).status_code == 422
    assert client.post(f"{API}/transactions", headers=h, json={"date": "2026-01-01", "description": "x", "amount": 1, "type": "expense", "user_id": "6" * 24}).status_code == 422
    assert client.post(f"{API}/optimization/run", headers=h, json={"months": 3, "min_spend": {"$where": 1}}).status_code in (201, 422)
    assert client.post(f"{API}/auth/register", json={"email": "x@example.com", "password": PASSWORD, "name": "x", "token_version": 5}).status_code == 422


def test_passwords_hashed_and_no_credentials_stored_anywhere(client, mongo):
    h = register(client, password="Sup3r-Secret!pw")
    import_statement(client, h, make_statement(1, recent_start(1)))
    from app.db import get_db

    db = get_db()
    user = db.users.find_one({})
    assert user["password_hash"].startswith("$argon2id$") and "Sup3r-Secret" not in user["password_hash"]
    assert set(user) == {"_id", "email", "name", "password_hash", "currency", "monthly_income", "current_savings", "savings_target_pct", "token_version", "created_at"}
    dump = json.dumps([d for name in db.list_collection_names() for d in db[name].find({})], default=str)
    assert "Sup3r-Secret" not in dump and "access_token" not in dump and get_settings().jwt_secret not in dump
    me = client.get(f"{API}/profile", headers=h).text
    assert "password" not in me and "argon2" not in me
    from app.security import verify_password

    assert verify_password("Sup3r-Secret!pw", user["password_hash"]) and not verify_password("sup3r-secret!pw", user["password_hash"])
    other = register(client, "other@example.com", "Sup3r-Secret!pw")
    assert db.users.distinct("password_hash").__len__() == 2 and other  # same password -> different salted hashes


def test_login_errors_do_not_reveal_whether_an_account_exists(client):
    register(client)
    a = client.post(f"{API}/auth/login", json={"email": "alice@example.com", "password": "wrong-pass1"})
    b = client.post(f"{API}/auth/login", json={"email": "nobody@example.com", "password": "wrong-pass1"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


# ---------------- uploads ----------------
def make_zip_bomb_xlsx(uncompressed_mb: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", b"0" * (uncompressed_mb * 1024 * 1024))
    return buf.getvalue()


def test_xlsx_zip_bomb_is_rejected_before_parsing(client):
    h = register(client)
    bomb = make_zip_bomb_xlsx(120)
    assert len(bomb) < 400_000  # tiny on the wire (well under the 5 MB upload limit) ...
    r = client.post(f"{API}/imports/preview", headers=h, files={"file": ("bomb.xlsx", bomb)})
    assert r.status_code == 422 and "too large when unpacked" in r.json()["detail"]  # ... but refused for its unpacked size


def test_upload_validation_matrix(client):
    h = register(client)
    send = lambda name, data: client.post(f"{API}/imports/preview", headers=h, files={"file": (name, data)})  # noqa: E731
    assert send("a.pdf", b"%PDF-1.4").status_code == 422
    assert send("a.csv.exe", b"Date,Description,Debit\n1,2,3").status_code == 422
    assert send("noext", b"Date,Description,Debit\n1,2,3").status_code == 422
    assert send("a.xlsx", b"Date,Description\nnot a zip").status_code == 422
    assert send("a.csv", b"\x00\x01\x02binary" * 100).status_code == 422
    assert send("a.csv", b"PK\x03\x04" + b"0" * 50).status_code == 422
    r = client.post(f"{API}/imports/preview", headers=h)
    assert r.status_code == 422  # missing file field
    before = set(os.listdir("."))
    ok = send("../../etc/passwd.csv", csv_bytes(["2026-01-01,Shop,10.00,"]))
    assert ok.status_code == 200 and set(os.listdir(".")) == before  # path-like filename never touches the filesystem
    assert client.get(f"{API}/imports", headers=h).json()["items"][0]["filename"] == "../../etc/passwd.csv"  # stored only as inert text, never used as a path


def test_formula_injection_neutralised_in_exports(client):
    h = register(client)
    client.post(f"{API}/transactions", headers=h, json={"date": "2026-01-01", "description": '=cmd|" /C calc"!A0', "amount": 100, "type": "expense", "notes": "@SUM(1+1)"})
    csv_text = client.get(f"{API}/reports/transactions.csv", headers=h).text
    assert "'=cmd" in csv_text and "'@SUM" in csv_text and ',=cmd' not in csv_text


# ---------------- logs ----------------
SENSITIVE = ["Sup3r-Secret!pw", "ZXQ-UNIQUE-MERCHANT-NARRATION", "9876543210123456", "Salary ACME"]


def test_sensitive_data_never_written_to_logs(client, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    h = register(client, "log@example.com", "Sup3r-Secret!pw")
    client.post(f"{API}/auth/login", json={"email": "log@example.com", "password": "wrong-Sup3r-Secret!pw"})
    secret_rows = ["2026-01-01,ZXQ-UNIQUE-MERCHANT-NARRATION 9876543210123456,10.00,", "bad-date,ZXQ-UNIQUE-MERCHANT-NARRATION,xx,", "2026-01-02,Salary ACME,,5000.00"]
    p = client.post(f"{API}/imports/preview", headers=h, files={"file": ("s.csv", csv_bytes(secret_rows))}).json()
    client.post(f"{API}/imports/{p['import_id']}/commit", headers=h, json={})
    client.get(f"{API}/transactions", headers=h, params={"q": "ZXQ-UNIQUE-MERCHANT-NARRATION"})
    client.post(f"{API}/transactions", headers=h, json={"date": "2026-01-03", "description": "ZXQ-UNIQUE-MERCHANT-NARRATION", "amount": 5, "type": "expense"})
    client.get(f"{API}/reports/monthly.pdf", headers=h)
    # force the two error paths that log: DB failure and an unexpected exception carrying sensitive text in its message
    from pymongo.errors import ServerSelectionTimeoutError

    import app.routers.transactions as tx_mod

    def db_boom(*a, **k):
        raise ServerSelectionTimeoutError("mongodb+srv://user:Sup3r-Secret!pw@cluster/ZXQ-UNIQUE-MERCHANT-NARRATION")

    monkeypatch.setattr(tx_mod, "tx_out", db_boom)
    assert client.get(f"{API}/transactions", headers=h).status_code == 503

    def boom(*a, **k):
        raise ValueError("ZXQ-UNIQUE-MERCHANT-NARRATION 9876543210123456 Sup3r-Secret!pw")

    monkeypatch.setattr(tx_mod, "tx_out", boom)
    r = client.get(f"{API}/transactions", headers=h)
    assert r.status_code == 500 and r.json() == {"detail": "Internal server error"} and "ZXQ" not in r.text
    token = h["Authorization"][7:]
    # Server-side loggers only: httpx/asyncio records come from the TEST client, not from the application.
    server = [r for r in caplog.records if r.name.split(".")[0] in {"financeai", "uvicorn", "app", "pymongo", "starlette", "fastapi"}]
    assert any(r.name == "financeai" for r in server)
    text = "\n".join(f"{rec.name} {rec.getMessage()} {rec.exc_text or ''}" for rec in server)
    for needle in SENSITIVE + [token, get_settings().jwt_secret]:
        assert needle not in text, f"leaked into logs: {needle!r}"
    assert "Unhandled ValueError" in text  # the error IS logged (type + location), just not its content


def test_error_responses_do_not_leak_internals(client):
    h = register(client)
    for r in (client.get(f"{API}/transactions/zzz", headers=h), client.get(f"{API}/nope", headers=h), client.post(f"{API}/transactions", headers=h, json={"amount": "abc"}),
              client.get(f"{API}/budgets/2026-99", headers=h)):
        assert r.status_code in (404, 422) and "Traceback" not in r.text and "pymongo" not in r.text.lower() and "/backend/" not in r.text and "\\app\\" not in r.text
    assert client.get("/openapi.json").status_code == 200  # dev only; production disables it (see test below)


def test_production_disables_docs_and_debug(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "p" * 48)
    from fastapi.testclient import TestClient

    from app.config import get_settings as gs
    from app.main import create_app

    gs.cache_clear()
    try:
        c = TestClient(create_app())
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404 and c.get("/debug/memory").status_code == 404
        assert "strict-transport-security" in c.get("/health").headers
    finally:
        gs.cache_clear()
    monkeypatch.setenv("JWT_SECRET", "change-me-short")
    with pytest.raises(RuntimeError):
        gs()
    gs.cache_clear()
