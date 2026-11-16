import time

import jwt

from app.config import get_settings
from tests.conftest import API, import_statement, make_statement, register


def test_health_is_lightweight(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_ready_ok_with_db(client):
    assert client.get("/ready").status_code == 200


def test_register_login_profile(client):
    h = register(client)
    r = client.get(f"{API}/profile", headers=h)
    assert r.status_code == 200 and r.json()["email"] == "alice@example.com"
    assert "password" not in r.text
    ok = client.post(f"{API}/auth/login", json={"email": "ALICE@example.com", "password": "Passw0rd!x"})
    assert ok.status_code == 200 and ok.json()["access_token"]


def test_duplicate_email_rejected(client):
    register(client)
    r = client.post(f"{API}/auth/register", json={"email": "Alice@Example.com", "password": "Passw0rd!x", "name": "A2"})
    assert r.status_code == 409


def test_weak_password_rejected(client):
    r = client.post(f"{API}/auth/register", json={"email": "b@example.com", "password": "12345678", "name": "B"})
    assert r.status_code == 422


def test_invalid_login(client):
    register(client)
    assert client.post(f"{API}/auth/login", json={"email": "alice@example.com", "password": "wrong-pass1"}).status_code == 401
    assert client.post(f"{API}/auth/login", json={"email": "nobody@example.com", "password": "wrong-pass1"}).status_code == 401


def test_protected_endpoints_require_auth(client):
    for path in ("/profile", "/transactions", "/budgets", "/goals", "/analytics/dashboard", "/reports/monthly", "/recurring", "/forecasts"):
        assert client.get(f"{API}{path}").status_code == 401, path


def test_garbage_and_expired_tokens(client):
    assert client.get(f"{API}/profile", headers={"Authorization": "Bearer abc.def.ghi"}).status_code == 401
    s = get_settings()
    h = register(client)
    uid = client.get(f"{API}/profile", headers=h).json()["id"]
    expired = jwt.encode({"sub": uid, "tv": 0, "exp": int(time.time()) - 10}, s.jwt_secret, algorithm=s.jwt_algorithm)
    assert client.get(f"{API}/profile", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    wrong_key = jwt.encode({"sub": uid, "tv": 0, "exp": int(time.time()) + 100}, "x" * 40, algorithm="HS256")
    assert client.get(f"{API}/profile", headers={"Authorization": f"Bearer {wrong_key}"}).status_code == 401


def test_logout_invalidates_token(client):
    h = register(client)
    assert client.post(f"{API}/auth/logout", headers=h).status_code == 204
    assert client.get(f"{API}/profile", headers=h).status_code == 401


def test_profile_update_and_validation(client, auth):
    r = client.patch(f"{API}/profile", headers=auth, json={"monthly_income": 8000000, "currency": "usd", "current_savings": 500000})
    assert r.status_code == 200 and r.json()["currency"] == "USD" and r.json()["monthly_income"] == 8000000
    assert client.patch(f"{API}/profile", headers=auth, json={"currency": "XXX"}).status_code == 422
    assert client.patch(f"{API}/profile", headers=auth, json={"monthly_income": -5}).status_code == 422
    assert client.patch(f"{API}/profile", headers=auth, json={"unknown": 1}).status_code == 422


def test_rate_limit_on_auth(client, monkeypatch):
    from app.security import auth_limiter

    monkeypatch.setattr(get_settings(), "auth_rate_limit_per_minute", 3)
    auth_limiter.reset()
    codes = [client.post(f"{API}/auth/login", json={"email": "x@example.com", "password": "nope-nope1"}).status_code for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3] == 429


def test_security_headers(client):
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"


def test_cors_only_allowed_origin(client):
    ok = client.options(f"{API}/profile", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options(f"{API}/profile", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers


def test_data_isolation_between_users(client):
    a = register(client, "a@example.com")
    b = register(client, "b@example.com")
    import_statement(client, a, make_statement(4))
    tx_a = client.get(f"{API}/transactions", headers=a).json()["items"][0]
    assert client.get(f"{API}/transactions", headers=b).json()["total"] == 0
    assert client.get(f"{API}/transactions/{tx_a['id']}", headers=b).status_code == 404
    assert client.patch(f"{API}/transactions/{tx_a['id']}", headers=b, json={"notes": "hax"}).status_code == 404
    assert client.delete(f"{API}/transactions/{tx_a['id']}", headers=b).status_code == 404
    d = client.get(f"{API}/analytics/dashboard", headers=b).json()
    assert d["totals"]["income"] == 0 and d["totals"]["net_expense"] == 0
    g = client.post(f"{API}/goals", headers=a, json={"name": "Car", "target_amount": 100000, "current_amount": 0}).json()
    assert client.get(f"{API}/goals/{g['id']}", headers=b).status_code == 404
    assert client.delete(f"{API}/goals/{g['id']}", headers=b).status_code == 404
    assert client.get(f"{API}/reports/transactions.csv", headers=b).text.strip().count("\n") == 0
    # same statement imported by B is not treated as a duplicate of A's data
    _, res = import_statement(client, b, make_statement(4))
    assert res["inserted"] == 28 and res["skipped_duplicates"] == 0


def test_database_unavailable_returns_503(client, mongo, monkeypatch):
    from pymongo.errors import ServerSelectionTimeoutError

    h = register(client)

    def boom(*a, **k):
        raise ServerSelectionTimeoutError("no servers: mongodb+srv://user:secret@host")

    monkeypatch.setattr(mongo["financeai"]["transactions"].__class__, "count_documents", boom)
    r = client.get(f"{API}/transactions", headers=h)
    assert r.status_code == 503
    assert "secret" not in r.text and "mongodb" not in r.text.lower()


def test_ready_reports_db_down(client, monkeypatch):
    from app import db as database

    monkeypatch.setattr(database, "ping", lambda: False)
    assert client.get("/ready").status_code == 503


def test_unhandled_error_is_generic(client, monkeypatch):
    import app.routers.auth as auth_mod

    monkeypatch.setattr(auth_mod, "user_out", lambda u: 1 / 0)
    r = client.post(f"{API}/auth/register", json={"email": "z@example.com", "password": "Passw0rd!x", "name": "Z"})
    assert r.status_code == 500 and r.json() == {"detail": "Internal server error"}


def test_login_throttled_per_account_even_if_ip_changes(client, monkeypatch):
    from app.security import auth_limiter

    monkeypatch.setattr(get_settings(), "auth_rate_limit_per_minute", 3)
    auth_limiter.reset()
    codes = []
    for i in range(5):  # a spoofed X-Forwarded-For would change the apparent IP on every attempt
        r = client.post(f"{API}/auth/login", json={"email": "victim@example.com", "password": "guess-guess1"}, headers={"X-Forwarded-For": f"10.0.0.{i}"})
        codes.append(r.status_code)
    assert codes == [401, 401, 401, 429, 429]
