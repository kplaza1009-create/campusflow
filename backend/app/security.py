"""Password hashing, session tokens, and login rate limiting."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import RateLimitEvent, User, UserSession

_hasher = PasswordHasher()  # argon2id with the library's recommended parameters
_DUMMY_HASH = _hasher.hash("timing-equalizer-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(user: User | None, password: str) -> bool:
    """Check a password. Runs a hash even when the user doesn't exist, so response
    timing doesn't reveal which emails have accounts."""
    try:
        return _hasher.verify(user.password_hash if user else _DUMMY_HASH, password) and bool(user)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(user: User) -> bool:
    return _hasher.check_needs_rehash(user.password_hash)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def tokens_match(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def create_session(db: Session, user: User, now: datetime) -> tuple[str, UserSession]:
    """Create a session row and return the raw token for the cookie.
    Only the SHA-256 of the token is stored, so a database leak can't be replayed."""
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    row = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(days=settings.session_idle_days),
    )
    db.add(row)
    return token, row


def touch_session(row: UserSession, now: datetime) -> None:
    """Sliding expiration, capped by an absolute lifetime. Written at most once an hour."""
    settings = get_settings()
    if now - row.last_seen_at < timedelta(hours=1):
        return
    absolute_end = row.created_at + timedelta(days=settings.session_absolute_days)
    row.last_seen_at = now
    row.expires_at = min(now + timedelta(days=settings.session_idle_days), absolute_end)


# --- Rate limiting -----------------------------------------------------------------
# Counted events live in PostgreSQL, so limits hold across several API instances.
# A transaction-scoped advisory lock per bucket serializes concurrent checks for the
# same key, so parallel requests can't all slip under the limit at once.


def lock_buckets(db: Session, *buckets: str) -> None:
    for bucket in sorted(buckets):  # consistent order avoids deadlocks
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:b))"), {"b": bucket})


def count_events(db: Session, bucket: str, since: datetime) -> int:
    stmt = select(func.count()).where(RateLimitEvent.bucket == bucket, RateLimitEvent.created_at > since)
    return db.scalar(stmt) or 0


def record_event(db: Session, bucket: str, now: datetime) -> None:
    db.add(RateLimitEvent(bucket=bucket, created_at=now))


def clear_events(db: Session, bucket: str) -> None:
    db.execute(delete(RateLimitEvent).where(RateLimitEvent.bucket == bucket))


def prune_events(db: Session, now: datetime) -> None:
    db.execute(delete(RateLimitEvent).where(RateLimitEvent.created_at < now - timedelta(days=1)))
