"""Registration, login, logout, and the current session."""

import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.deps import DB, CurrentAuth, Now, optional_auth
from app.models import User, UserSession
from app.schemas import Credentials, Registration, SessionOut, UserOut
from app.security import (
    clear_events,
    count_events,
    create_session,
    hash_password,
    lock_buckets,
    needs_rehash,
    prune_events,
    record_event,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
log = logging.getLogger("campusflow.auth")

TOO_MANY = "Too many attempts. Please wait a few minutes and try again."


def _client_ip(request: Request) -> str:
    # Behind a proxy, run uvicorn with --proxy-headers so this is the real client.
    return request.client.host if request.client else "unknown"


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.session_cookie_name,
        token,
        max_age=s.session_absolute_days * 86400,
        httponly=True,  # JavaScript can't read it, so XSS can't steal it
        secure=s.session_cookie_secure,
        samesite=s.session_cookie_samesite,  # type: ignore[arg-type]
        path="/",
    )


def _clear_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(
        s.session_cookie_name,
        path="/",
        secure=s.session_cookie_secure,
        httponly=True,
        samesite=s.session_cookie_samesite,  # type: ignore[arg-type]
    )


@router.get("/session", response_model=SessionOut)
def current_session(request: Request, db: DB, now: Now) -> SessionOut:
    auth = optional_auth(request, db, now)
    if auth is None:
        return SessionOut(user=None, csrf_token=None)
    return SessionOut(user=UserOut.model_validate(auth.user), csrf_token=auth.session.csrf_token)


@router.post("/register", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def register(body: Registration, request: Request, response: Response, db: DB, now: Now) -> SessionOut:
    s = get_settings()
    ip_bucket = f"register:ip:{_client_ip(request)}"
    lock_buckets(db, ip_bucket)
    if count_events(db, ip_bucket, now - timedelta(hours=1)) >= s.register_max_per_ip_per_hour:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)
    record_event(db, ip_bucket, now)

    user = User(
        email=body.email.lower(), password_hash=hash_password(body.password), time_zone=body.time_zone
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "An account with this email already exists. Try signing in."
        ) from None

    token, session = create_session(db, user, now)
    db.commit()
    _set_cookie(response, token)
    log.info("user registered user_id=%s", user.id)
    return SessionOut(user=UserOut.model_validate(user), csrf_token=session.csrf_token)


@router.post("/login", response_model=SessionOut)
def login(body: Credentials, request: Request, response: Response, db: DB, now: Now) -> SessionOut:
    s = get_settings()
    email = body.email.lower()
    email_bucket = f"login:email:{email}"
    ip_bucket = f"login:ip:{_client_ip(request)}"
    since = now - timedelta(minutes=s.login_window_minutes)

    lock_buckets(db, email_bucket, ip_bucket)
    if (
        count_events(db, email_bucket, since) >= s.login_max_failures_per_email
        or count_events(db, ip_bucket, since) >= s.login_max_failures_per_ip
    ):
        log.warning("login throttled ip_bucket=%s", ip_bucket)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)

    user = db.scalar(select(User).where(User.email == email))
    if not verify_password(user, body.password) or user is None:
        record_event(db, email_bucket, now)
        record_event(db, ip_bucket, now)
        prune_events(db, now)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")

    if needs_rehash(user):
        user.password_hash = hash_password(body.password)
    clear_events(db, email_bucket)
    token, session = create_session(db, user, now)
    db.commit()
    _set_cookie(response, token)
    log.info("login succeeded user_id=%s", user.id)
    return SessionOut(user=UserOut.model_validate(user), csrf_token=session.csrf_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(auth: CurrentAuth, response: Response, db: DB) -> None:
    db.execute(delete(UserSession).where(UserSession.id == auth.session.id))
    db.commit()
    _clear_cookie(response)
