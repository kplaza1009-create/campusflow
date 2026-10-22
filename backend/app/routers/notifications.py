"""In-app notifications: list, mark read. The worker creates them; users only read them."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select, update

from app.deps import DB, CurrentAuth, Now
from app.models import Notification
from app.schemas import NotificationList, NotificationOut

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=NotificationList)
def list_notifications(
    auth: CurrentAuth, db: DB, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> NotificationList:
    mine = Notification.user_id == auth.user.id
    unread = db.scalar(select(func.count()).where(mine, Notification.read_at.is_(None))) or 0
    rows = db.scalars(
        select(Notification)
        .where(mine)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    )
    return NotificationList(unread=unread, items=[NotificationOut.model_validate(n) for n in rows])


@router.post("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: int, auth: CurrentAuth, db: DB, now: Now) -> Notification:
    n = db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == auth.user.id)
    )
    if n is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found.")
    if n.read_at is None:
        n.read_at = now
        db.commit()
    return n


@router.post("/read-all", status_code=status.HTTP_204_NO_CONTENT)
def mark_all_read(auth: CurrentAuth, db: DB, now: Now) -> None:
    db.execute(
        update(Notification)
        .where(Notification.user_id == auth.user.id, Notification.read_at.is_(None))
        .values(read_at=now)
    )
    db.commit()
