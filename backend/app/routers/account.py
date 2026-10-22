"""Account settings: time zone, password, and account deletion."""

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import delete

from app.deps import DB, CurrentAuth
from app.models import User, UserSession
from app.routers.auth import _clear_cookie
from app.schemas import AccountDeletion, PasswordChange, SettingsUpdate, UserOut
from app.security import hash_password, verify_password

router = APIRouter(prefix="/api/account", tags=["account"])


@router.patch("", response_model=UserOut)
def update_settings(body: SettingsUpdate, auth: CurrentAuth, db: DB) -> User:
    # Exact deadlines are stored as UTC instants and date-only deadlines as dates,
    # so changing the zone only changes how they're displayed and when dates end.
    auth.user.time_zone = body.time_zone
    db.commit()
    return auth.user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: PasswordChange, auth: CurrentAuth, db: DB) -> None:
    if not verify_password(auth.user, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your current password is incorrect.")
    auth.user.password_hash = hash_password(body.new_password)
    # Sign out every other device; keep this one.
    db.execute(
        delete(UserSession).where(UserSession.user_id == auth.user.id, UserSession.id != auth.session.id)
    )
    db.commit()


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(body: AccountDeletion, auth: CurrentAuth, db: DB, response: Response) -> None:
    if not verify_password(auth.user, body.password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Your password is incorrect.")
    # Courses, assignments, and sessions are removed by ON DELETE CASCADE.
    db.delete(auth.user)
    db.commit()
    _clear_cookie(response)
