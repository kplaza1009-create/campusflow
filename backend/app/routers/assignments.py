"""Assignments: create, edit, complete, reopen, delete, filter, plus the Today view."""

from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import ColumnElement, Date, and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import TIMESTAMP, insert
from sqlalchemy.orm import Session

from app.calendar import shaped_window
from app.deps import DB, CurrentAuth, Now
from app.idempotency import IdempotencyKey
from app.models import Assignment, Course, Reminder
from app.reminders import sync_assignment
from app.routers.courses import get_owned_course
from app.schemas import (
    AssignmentCompletion,
    AssignmentCreate,
    AssignmentFields,
    AssignmentOut,
    AssignmentUpdate,
    CourseBrief,
    DuplicateWarning,
    ReminderOut,
    TodayOut,
)
from app.timeutil import (
    NonexistentLocalTime,
    end_of_date,
    local_today,
    localize,
    start_of_date,
    zone,
)

router = APIRouter(prefix="/api", tags=["assignments"])

UPCOMING_DAYS = 7


# --- Deadline rules (shared by filters, sorting, and the Today view) --------------


def deadline_sql(tz: str) -> ColumnElement[datetime]:
    """The instant an assignment becomes overdue, computed in SQL.

    Timed: its exact UTC instant. Date-only: local midnight after the due date in
    the user's *current* zone, so a date never shifts when the zone changes.
    """
    end_of_due_date = func.timezone(tz, cast(Assignment.due_date + 1, TIMESTAMP))
    return func.coalesce(Assignment.due_at, end_of_due_date)


def local_due_date_sql(tz: str) -> ColumnElement[date]:
    return func.coalesce(Assignment.due_date, cast(func.timezone(tz, Assignment.due_at), Date))


def to_out(a: Assignment, tz: str, now: datetime, ambiguous: bool = False) -> AssignmentOut:
    if a.due_at is not None:
        local = a.due_at.astimezone(zone(tz))
        due_date, due_time, deadline = local.date(), local.time().replace(tzinfo=None), a.due_at
    else:
        assert a.due_date is not None
        due_date, due_time, deadline = a.due_date, None, end_of_date(a.due_date, tz)
    c = a.course
    return AssignmentOut(
        id=a.id,
        title=a.title,
        description=a.description,
        course=CourseBrief(
            id=c.id, name=c.name, code=c.code, color=c.color, archived=c.archived_at is not None
        ),
        due_date=due_date,
        due_time=due_time,
        due_at=a.due_at,
        all_day=a.due_at is None,
        deadline=deadline,
        overdue=a.completed_at is None and now >= deadline,
        completed=a.completed_at is not None,
        completed_at=a.completed_at,
        reminder_minutes=a.reminder_minutes,
        time_note="ambiguous_earlier" if ambiguous else None,
    )


# --- Helpers ----------------------------------------------------------------------


def _owned(db: Session, user_id: int, assignment_id: int) -> Assignment:
    a = db.scalar(select(Assignment).where(Assignment.id == assignment_id, Assignment.user_id == user_id))
    if a is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found.")
    return a


def _resolve_due(body: AssignmentFields, tz: str) -> tuple[date | None, datetime | None, bool]:
    """Turn the form's local date (+ optional time) into stored columns."""
    if body.due_time is None:
        return body.due_date, None, False
    try:
        loc = localize(body.due_date, body.due_time, tz)
    except NonexistentLocalTime as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    return None, loc.instant, loc.ambiguous


def _check_course(db: Session, user_id: int, course_id: int) -> Course:
    """The course must exist, belong to this user, and not be archived."""
    course = get_owned_course(db, user_id, course_id)
    if course.archived_at is not None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "That course is archived. Restore it before adding assignments.",
        )
    return course


def _likely_duplicates(
    db: Session, user_id: int, body: AssignmentFields, tz: str, local_date: date, exclude_id: int | None
) -> list[Assignment]:
    """Same course, same title (trimmed, case-insensitive), same due date."""
    stmt = select(Assignment).where(
        Assignment.user_id == user_id,
        Assignment.course_id == body.course_id,
        func.lower(Assignment.title) == body.title.lower(),
        local_due_date_sql(tz) == local_date,
    )
    if exclude_id is not None:
        stmt = stmt.where(Assignment.id != exclude_id)
    return list(db.scalars(stmt.limit(5)))


def _duplicate_response(matches: list[Assignment], tz: str, now: datetime) -> JSONResponse:
    warning = DuplicateWarning(
        message="This looks like an assignment you already have: same course, title, and due date.",
        matches=[to_out(m, tz, now) for m in matches],
    )
    return JSONResponse(warning.model_dump(mode="json"), status_code=status.HTTP_409_CONFLICT)


# --- Endpoints --------------------------------------------------------------------

Sort = Literal["due_asc", "due_desc"]
StatusFilter = Literal["open", "completed", "all"]


@router.get("/assignments", response_model=list[AssignmentOut])
def list_assignments(
    auth: CurrentAuth,
    db: DB,
    now: Now,
    course_id: int | None = None,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = "all",
    overdue: bool | None = None,
    due_from: date | None = None,
    due_to: date | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    sort: Sort = "due_asc",
    include_archived: bool = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> list[AssignmentOut]:
    tz = auth.user.time_zone
    deadline = deadline_sql(tz)
    stmt = select(Assignment).join(
        Course, and_(Course.id == Assignment.course_id, Course.user_id == Assignment.user_id)
    )
    stmt = stmt.where(Assignment.user_id == auth.user.id)

    if course_id is not None:
        stmt = stmt.where(Assignment.course_id == course_id)
    if not include_archived:
        stmt = stmt.where(Course.archived_at.is_(None))
    if status_filter == "open":
        stmt = stmt.where(Assignment.completed_at.is_(None))
    elif status_filter == "completed":
        stmt = stmt.where(Assignment.completed_at.is_not(None))
    if overdue is True:
        stmt = stmt.where(Assignment.completed_at.is_(None), deadline <= now)
    elif overdue is False:
        stmt = stmt.where(or_(Assignment.completed_at.is_not(None), deadline > now))
    if due_from is not None:
        stmt = stmt.where(
            or_(Assignment.due_date >= due_from, Assignment.due_at >= start_of_date(due_from, tz))
        )
    if due_to is not None:
        stmt = stmt.where(or_(Assignment.due_date <= due_to, Assignment.due_at < end_of_date(due_to, tz)))
    if q and q.strip():
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Assignment.title.ilike(f"%{escaped}%", escape="\\"))

    order = deadline.asc() if sort == "due_asc" else deadline.desc()
    stmt = stmt.order_by(order, Assignment.id).limit(limit)
    return [to_out(a, tz, now) for a in db.scalars(stmt)]


@router.get("/today", response_model=TodayOut)
def today_view(auth: CurrentAuth, db: DB, now: Now) -> TodayOut:
    tz = auth.user.time_zone
    today = local_today(now, tz)
    deadline = deadline_sql(tz)
    stmt = (
        select(Assignment)
        .join(Course, and_(Course.id == Assignment.course_id, Course.user_id == Assignment.user_id))
        .where(
            Assignment.user_id == auth.user.id,
            Assignment.completed_at.is_(None),
            Course.archived_at.is_(None),
            # Everything overdue, plus anything due through the next 7 local dates.
            local_due_date_sql(tz) <= today + timedelta(days=UPCOMING_DAYS),
        )
        .order_by(deadline, Assignment.id)
    )
    overdue, due_today, upcoming = [], [], []
    for a in db.scalars(stmt):
        out = to_out(a, tz, now)
        if out.overdue:
            overdue.append(out)
        elif out.due_date == today:
            due_today.append(out)
        else:
            upcoming.append(out)
    events = shaped_window(db, auth.user.id, tz, start_of_date(today, tz), end_of_date(today, tz))
    return TodayOut(
        today=today, time_zone=tz, overdue=overdue, due_today=due_today, upcoming=upcoming, events=events
    )


@router.get("/assignments/{assignment_id}", response_model=AssignmentOut)
def get_assignment(assignment_id: int, auth: CurrentAuth, db: DB, now: Now) -> AssignmentOut:
    return to_out(_owned(db, auth.user.id, assignment_id), auth.user.time_zone, now)


@router.post(
    "/assignments",
    response_model=AssignmentOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": DuplicateWarning}},
)
def create_assignment(
    body: AssignmentCreate,
    auth: CurrentAuth,
    db: DB,
    now: Now,
    response: Response,
    key: IdempotencyKey = None,
) -> AssignmentOut | JSONResponse:
    tz, user_id = auth.user.time_zone, auth.user.id

    # 1. A retried request returns what the first one created.
    if key is not None:
        existing = db.scalar(
            select(Assignment).where(Assignment.user_id == user_id, Assignment.idempotency_key == key)
        )
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            response.headers["Idempotent-Replayed"] = "true"
            return to_out(existing, tz, now)

    # 2. Validate ownership and the deadline.
    _check_course(db, user_id, body.course_id)
    due_date, due_at, ambiguous = _resolve_due(body, tz)
    local_date = due_date or due_at.astimezone(zone(tz)).date()  # type: ignore[union-attr]

    # 3. Warn about a likely duplicate unless the user already chose to save anyway.
    if not body.allow_duplicate:
        matches = _likely_duplicates(db, user_id, body, tz, local_date, exclude_id=None)
        # A concurrent retry with the same key may have just saved the "duplicate":
        # that's this same request, so replay it instead of warning.
        replay = next((m for m in matches if key is not None and m.idempotency_key == key), None)
        if replay is not None:
            response.status_code = status.HTTP_200_OK
            response.headers["Idempotent-Replayed"] = "true"
            return to_out(replay, tz, now)
        if matches:
            return _duplicate_response(matches, tz, now)

    # 4. Insert. If a concurrent retry with the same key won the race, return its row.
    new_id = db.scalar(
        insert(Assignment)
        .values(
            user_id=user_id,
            course_id=body.course_id,
            title=body.title,
            description=body.description,
            due_date=due_date,
            due_at=due_at,
            reminder_minutes=body.reminder_minutes,
            idempotency_key=key,
        )
        .on_conflict_do_nothing(constraint="uq_assignments_idempotency")
        .returning(Assignment.id)
    )
    if new_id is not None:
        sync_assignment(db, new_id, auth.user, now)  # same transaction as the insert
    db.commit()
    if new_id is None:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
        existing = db.scalar(
            select(Assignment).where(Assignment.user_id == user_id, Assignment.idempotency_key == key)
        )
        assert existing is not None
        return to_out(existing, tz, now)
    return to_out(_owned(db, user_id, new_id), tz, now, ambiguous)


@router.put(
    "/assignments/{assignment_id}",
    response_model=AssignmentOut,
    responses={409: {"model": DuplicateWarning}},
)
def update_assignment(
    assignment_id: int, body: AssignmentUpdate, auth: CurrentAuth, db: DB, now: Now
) -> AssignmentOut | JSONResponse:
    tz, user_id = auth.user.time_zone, auth.user.id
    a = _owned(db, user_id, assignment_id)
    if body.course_id != a.course_id:
        _check_course(db, user_id, body.course_id)
    else:
        get_owned_course(db, user_id, body.course_id)
    due_date, due_at, ambiguous = _resolve_due(body, tz)
    local_date = due_date or due_at.astimezone(zone(tz)).date()  # type: ignore[union-attr]

    if not body.allow_duplicate:
        matches = _likely_duplicates(db, user_id, body, tz, local_date, exclude_id=a.id)
        if matches:
            return _duplicate_response(matches, tz, now)

    a.course_id, a.title, a.description = body.course_id, body.title, body.description
    a.due_date, a.due_at, a.reminder_minutes = due_date, due_at, body.reminder_minutes
    db.flush()
    sync_assignment(db, a.id, auth.user, now)
    db.commit()
    db.refresh(a)
    return to_out(a, tz, now, ambiguous)


@router.post("/assignments/{assignment_id}/completion", response_model=AssignmentOut)
def set_completion(
    assignment_id: int, body: AssignmentCompletion, auth: CurrentAuth, db: DB, now: Now
) -> AssignmentOut:
    a = _owned(db, auth.user.id, assignment_id)
    if body.completed and a.completed_at is None:
        a.completed_at = now
    elif not body.completed:
        a.completed_at = None
    db.flush()
    sync_assignment(db, a.id, auth.user, now)  # completing cancels reminders; reopening restores them
    db.commit()
    return to_out(a, auth.user.time_zone, now)


@router.delete("/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assignment(assignment_id: int, auth: CurrentAuth, db: DB) -> None:
    # Its reminders go with it (ON DELETE CASCADE); notifications keep their text.
    db.delete(_owned(db, auth.user.id, assignment_id))
    db.commit()


@router.get("/assignments/{assignment_id}/reminders", response_model=list[ReminderOut])
def assignment_reminders(assignment_id: int, auth: CurrentAuth, db: DB) -> list[Reminder]:
    """Scheduling and delivery history, newest first, for the edit dialog."""
    _owned(db, auth.user.id, assignment_id)
    return list(
        db.scalars(
            select(Reminder)
            .where(Reminder.user_id == auth.user.id, Reminder.assignment_id == assignment_id)
            .order_by(Reminder.created_at.desc(), Reminder.id.desc())
            .limit(10)
        )
    )
