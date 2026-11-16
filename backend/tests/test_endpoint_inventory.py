"""Authentication coverage generated from the OpenAPI schema, so a newly added endpoint can never be forgotten.

Every operation under /api/v1 must answer 401 to an unauthenticated caller, except the two documented public auth endpoints.
(The older hard-coded lists in test_security_integrity.py stay as they are; this is an additional safety net.)
"""
import re

from tests.conftest import API, register

PUBLIC = {("post", "/api/v1/auth/register"), ("post", "/api/v1/auth/login")}
OBJECT_ID = "0" * 24


def operations(client):
    spec = client.get("/openapi.json").json()
    out = []
    for path, item in spec["paths"].items():
        if not path.startswith("/api/v1/"):
            continue
        for method in item:
            if method in ("get", "post", "put", "patch", "delete"):
                out.append((method, path))
    return sorted(out)


def concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", OBJECT_ID, path)


def test_inventory_is_not_vacuous(client):
    ops = operations(client)
    paths = {p for _, p in ops}
    assert len(ops) >= 60                                                    # guards against the schema silently shrinking to nothing
    for must in ("/api/v1/slips/batches", "/api/v1/slips/batches/{batch_id}/commit", "/api/v1/simulations/run", "/api/v1/simulations/loan",
                 "/api/v1/transactions", "/api/v1/imports/preview", "/api/v1/reports/monthly.pdf", "/api/v1/analytics/dashboard"):
        assert must in paths, must


def test_every_endpoint_rejects_unauthenticated_requests(client):
    failures = []
    for method, path in operations(client):
        if (method, path) in PUBLIC:
            continue
        r = getattr(client, method)(concrete(path), **({} if method in ("get", "delete") else {"json": {}}))
        if r.status_code != 401:
            failures.append((method.upper(), path, r.status_code))
    assert not failures, f"endpoints reachable without authentication: {failures}"


def test_only_the_two_public_auth_endpoints_work_without_a_token(client):
    assert client.post(f"{API}/auth/register", json={}).status_code == 422            # reachable (validation), i.e. public
    assert client.post(f"{API}/auth/login", json={}).status_code == 422


def test_every_endpoint_rejects_a_forged_token(client):
    bad = {"Authorization": "Bearer not.a.jwt"}
    failures = []
    for method, path in operations(client):
        if (method, path) in PUBLIC:
            continue
        r = getattr(client, method)(concrete(path), headers=bad, **({} if method in ("get", "delete") else {"json": {}}))
        if r.status_code != 401:
            failures.append((method.upper(), path, r.status_code))
    assert not failures, failures


def test_authenticated_unknown_ids_never_500(client):
    """With a valid token, id-based endpoints must answer 404/422/409 for ids that do not exist, never a 500."""
    h = register(client)
    failures = []
    for method, path in operations(client):
        if (method, path) in PUBLIC or "/auth/" in path or path.endswith(("/reset", "/logout")):
            continue
        if "{" not in path:
            continue
        r = getattr(client, method)(concrete(path), headers=h, **({} if method in ("get", "delete") else {"json": {}}))
        if r.status_code >= 500:
            failures.append((method.upper(), path, r.status_code))
    assert not failures, failures


def test_chunked_request_bodies_without_a_length_are_refused(client):
    """The size limit keys off Content-Length; a chunked body without one must not reach the multipart parser."""
    h = register(client)
    chunks = (b"x" * 1024 for _ in range(4))
    r = client.post(f"{API}/imports/preview", headers=h, content=chunks)       # httpx sends a generator as Transfer-Encoding: chunked
    assert r.status_code == 411
    # normal requests (with a length) are unaffected
    assert client.post(f"{API}/imports/preview", headers=h, files={"file": ("a.csv", b"Date,Description,Debit,Credit\n2026-01-01,X,1.00,\n")}).status_code == 200
    assert client.get(f"{API}/profile", headers=h).status_code == 200
