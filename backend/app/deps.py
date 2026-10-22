"""Request dependencies: the clock, the authenticated session, and CSRF checks."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User, UserSession
from app.security import hash_token, tokens_match, touch_session

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_now() -> datetime:
    """The current instant. Tests override this dependency to freeze time."""
    return datetime.now(UTC)


DB = Annotated[Session, Depends(get_db)]
Now = Annotated[datetime, Depends(get_now)]


@dataclass
class Auth:
    user: User
    session: UserSession


def _load_session(request: Request, db: Session, now: datetime) -> UserSession | None:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        return None
    row = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if row is None or row.expires_at <= now:
        return None
    return row


def optional_auth(request: Request, db: DB, now: Now) -> Auth | None:
    row = _load_session(request, db, now)
    return Auth(user=row.user, session=row) if row else None


def require_auth(request: Request, db: DB, now: Now) -> Auth:
    """The signed-in user, derived only from the session cookie.

    For state-changing requests the X-CSRF-Token header must match the token issued
    with this session. A cross-site form or script can make the browser send the
    cookie, but it can't read our token, so it can't forge the header.
    """
    row = _load_session(request, db, now)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not tokens_match(sent, row.csrf_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Security token missing or invalid.")
    touch_session(row, now)
    db.commit()
    return Auth(user=row.user, session=row)


CurrentAuth = Annotated[Auth, Depends(require_auth)]
