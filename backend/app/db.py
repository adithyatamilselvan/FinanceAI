"""Shared MongoDB client with lifecycle handling and index setup.

A single synchronous PyMongo client (thread-safe, pooled) is created at startup and closed at shutdown.
Endpoints that touch the database are plain `def` endpoints so FastAPI runs them in its threadpool and the
event loop is never blocked.
"""
import logging
import threading
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import PyMongoError

from .config import get_settings

log = logging.getLogger("financeai.db")

_client: Any = None
_lock = threading.Lock()
_indexes_ready = False


class DatabaseUnavailable(Exception):
    """Raised when the database cannot be reached."""


def set_client_for_tests(client: Any) -> None:
    global _client, _indexes_ready
    _client = client
    _indexes_ready = False


def connect() -> None:
    global _client
    s = get_settings()
    if _client is None:
        _client = MongoClient(
            s.mongodb_uri,
            serverSelectionTimeoutMS=s.mongodb_timeout_ms,
            maxPoolSize=10,
            appname="financeai",
            tz_aware=True,
        )


def close() -> None:
    global _client, _indexes_ready
    if _client is not None:
        try:
            _client.close()
        finally:
            _client = None
            _indexes_ready = False


def ping() -> bool:
    if _client is None:
        return False
    try:
        _client.admin.command("ping")
        return True
    except PyMongoError:
        return False


def ensure_indexes() -> None:
    """Create indexes (idempotent). Raises PyMongoError if the database is unreachable."""
    global _indexes_ready
    db = _client[get_settings().mongodb_db]
    db.users.create_index([("email", ASCENDING)], unique=True, name="uniq_email")
    db.transactions.create_index([("user_id", ASCENDING), ("date", DESCENDING)], name="user_date")
    db.transactions.create_index([("user_id", ASCENDING), ("category", ASCENDING), ("date", DESCENDING)], name="user_category")
    db.transactions.create_index([("user_id", ASCENDING), ("fingerprint", ASCENDING)], unique=True, name="uniq_user_fingerprint")
    db.transactions.create_index([("user_id", ASCENDING), ("import_id", ASCENDING)], name="user_import")
    db.categories.create_index([("user_id", ASCENDING), ("kind", ASCENDING), ("name", ASCENDING)], name="user_kind_name")
    db.bank_imports.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_created")
    db.bank_imports.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_staged", partialFilterExpression={"status": "staged"})
    db.budgets.create_index([("user_id", ASCENDING), ("period", ASCENDING)], unique=True, name="uniq_user_period")
    db.recurring_payments.create_index([("user_id", ASCENDING), ("merchant_key", ASCENDING)], unique=True, name="uniq_user_merchant")
    db.financial_goals.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_goal")
    db.forecasts.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_created")
    db.simulations.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_created")
    db.recommendations.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_created")
    db.slip_batches.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)], name="user_created")
    db.slip_batches.create_index([("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_expiry")
    _indexes_ready = True


def get_db():
    """FastAPI dependency / helper returning the database handle; ensures indexes exist."""
    global _indexes_ready
    if _client is None:
        raise DatabaseUnavailable("Database client is not initialised")
    if not _indexes_ready:
        with _lock:
            if not _indexes_ready:
                try:
                    ensure_indexes()
                except PyMongoError as e:
                    log.warning("Index setup failed: %s", type(e).__name__)
                    raise DatabaseUnavailable("Database unavailable") from e
    return _client[get_settings().mongodb_db]
