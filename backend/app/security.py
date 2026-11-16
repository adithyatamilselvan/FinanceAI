"""Password hashing (Argon2id), JWT helpers and a tiny in-memory rate limiter."""
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from .config import get_settings

# OWASP-recommended low-memory Argon2id profile (19 MiB) keeps RAM use modest under concurrency.
_ph = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
# A real hash used to equalise timing when the email is unknown.
_DUMMY_HASH = _ph.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    try:
        return _ph.verify(hashed or _DUMMY_HASH, password) and hashed is not None
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: str, token_version: int) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=s.jwt_expire_minutes)
    payload = {"sub": user_id, "tv": token_version, "iat": int(now.timestamp()), "exp": int(exp.timestamp())}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm), s.jwt_expire_minutes * 60


def decode_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm], options={"require": ["exp", "sub"]})


class RateLimiter:
    """Sliding-window limiter keyed by string. Single-process only (Cloud Run max instances = 1)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: int = 60) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 5000:  # bound memory
                for k in [k for k, v in self._hits.items() if not v][:1000]:
                    self._hits.pop(k, None)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


auth_limiter = RateLimiter()
