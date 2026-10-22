"""Calendar events (one-time and weekly classes), conflict warnings, and the week view."""

from datetime import date, time, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.calendar import course_brief, occurrence_out, rule_of, shaped_window, wall_end, window_occurrences
from app.deps import DB, CurrentAuth, Now
from app.idempotency import IdempotencyKey
from app.models import Assignment, Course, Event, User
from app.routers.assignments import deadline_sql, local_due_date_sql, to_out
from app.routers.courses import get_owned_course
from app.scheduling import Occurrence, conflicts, expand_weekly, local_start
from app.schemas import (
    MAX_EVENT_DAYS,
    CalendarOut,
    EventFields,
    EventOut,
    OneTimeSchedule,
    ScheduleConflict,
    WeeklySchedule,
)
from app.timeutil import NonexistentLocalTime, localize, start_of_date, zone

router = APIRouter(prefix="/api", tags=["calendar"])

MAX_LISTED_CONFLICTS = 10


# --- Turning a request into columns -------------------------------------------------


def _columns(body: EventFields, tz: str) -> tuple[dict[str, Any], bool]:
    """Stored columns for the event, plus whether an ambiguous local time was resolved."""
    s = body.schedule
    if isinstance(s, OneTimeSchedule):
        try:
            start = localize(s.start_date, s.start_time, tz)
            end = localize(s.end_date, s.end_time, tz)
        except NonexistentLocalTime as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
        if end.instant <= start.instant:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The end must be after the start.")
        if end.instant - start.instant > timedelta(days=MAX_EVENT_DAYS):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "An event can last at most two weeks.")
        return {
            "starts_at": start.instant,
            "ends_at": end.instant,
            "weekdays": None,
            "start_time": None,
            "duration_minutes": None,
            "time_zone": None,
            "first_date": None,
            "last_date": None,
        }, start.ambiguous or end.ambiguous
    minutes = (s.end_time.hour * 60 + s.end_time.minute - s.start_time.hour * 60 - s.start_time.minute) % (
        24 * 60
    )
    return {
        "starts_at": None,
        "ends_at": None,
        "weekdays": s.weekdays,
        "start_time": s.start_time,
        "duration_minutes": minutes,
        "time_zone": tz,
        "first_date": s.first_date,
        "last_date": s.last_date,
    }, False


def _candidate_occurrences(cols: dict[str, Any], event_id: int) -> list[Occurrence]:
    """Every occurrence the event would have (a weekly series is at most a year long)."""
    if cols["weekdays"] is None:
        return [Occurrence(cols["starts_at"], cols["ends_at"], event_id)]
    probe = Event(**cols)
    probe.id = event_id
    rule = rule_of(probe)
    start = local_start(rule.first_date, time(0), rule.time_zone)
    end = local_start(rule.last_date + timedelta(days=2), time(0), rule.time_zone)
    return list(expand_weekly(event_id, rule, start, end))


def _conflict_response(
    db: Session, user: User, cols: dict[str, Any], exclude_id: int | None
) -> JSONResponse | None:
    candidates = _candidate_occurrences(cols, exclude_id or -1)
    if not candidates:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "This class never meets: no chosen weekday falls between its dates.",
        )
    window_start = min(c.start for c in candidates)
    window_end = max(c.end for c in candidates)
    events, existing = window_occurrences(db, user.id, window_start, window_end, exclude_id)
    hits = sorted({e for _, e in conflicts(candidates, existing)})
    if not hits:
        return None
    warning = ScheduleConflict(
        message=f"This overlaps {len(hits)} other {'time' if len(hits) == 1 else 'times'} on your calendar.",
        conflicts=[
            occurrence_out(events[o.event_id], o, user.time_zone) for o in hits[:MAX_LISTED_CONFLICTS]
        ],
        total=len(hits),
    )
    return JSONResponse(warning.model_dump(mode="json"), status_code=status.HTTP_409_CONFLICT)


def _check_course(db: Session, user_id: int, course_id: int | None, current: int | None = None) -> None:
    if course_id is None:
        return
    course = get_owned_course(db, user_id, course_id)
    if course.archived_at is not None and course_id != current:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "That course is archived. Restore it before adding events."
        )


def _owned(db: Session, user_id: int, event_id: int) -> Event:
    e = db.scalar(select(Event).where(Event.id == event_id, Event.user_id == user_id))
    if e is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found.")
    return e


def event_out(e: Event, user_tz: str, ambiguous: bool = False) -> EventOut:
    schedule: OneTimeSchedule | WeeklySchedule
    ends_next_day = False
    if e.weekdays is None:
        assert e.starts_at is not None and e.ends_at is not None
        s, t = e.starts_at.astimezone(zone(user_tz)), e.ends_at.astimezone(zone(user_tz))
        schedule = OneTimeSchedule(
            start_date=s.date(),
            start_time=s.time().replace(tzinfo=None),
            end_date=t.date(),
            end_time=t.time().replace(tzinfo=None),
        )
        tz = user_tz
    else:
        assert e.start_time and e.duration_minutes and e.first_date and e.last_date and e.time_zone
        end_time, ends_next_day = wall_end(e.start_time, e.duration_minutes)
        schedule = WeeklySchedule(
            weekdays=e.weekdays,
            start_time=e.start_time,
            end_time=end_time,
            first_date=e.first_date,
            last_date=e.last_date,
        )
        tz = e.time_zone
    return EventOut(
        id=e.id,
        title=e.title,
        kind=e.kind,
        course=course_brief(e.course),
        location=e.location,
        recurring=e.recurring,
        schedule=schedule,
        time_zone=tz,
        ends_next_day=ends_next_day,
        time_note="ambiguous_earlier" if ambiguous else None,
    )


def _by_key(db: Session, user_id: int, key: UUID) -> Event | None:
    return db.scalar(select(Event).where(Event.user_id == user_id, Event.idempotency_key == key))


# --- Endpoints -------------------------------------------------------------------


@router.get("/calendar", response_model=CalendarOut)
def calendar(
    auth: CurrentAuth,
    db: DB,
    now: Now,
    start: date,
    days: Annotated[int, Query(ge=1, le=42)] = 7,
) -> CalendarOut:
    """Occurrences and deadlines for `days` local dates starting at `start` (in the user's zone)."""
    tz = auth.user.time_zone
    end = start + timedelta(days=days)
    window_start, window_end = start_of_date(start, tz), start_of_date(end, tz)
    occurrences = shaped_window(db, auth.user.id, tz, window_start, window_end)
    due = local_due_date_sql(tz)
    deadlines = db.scalars(
        select(Assignment)
        .join(Course, and_(Course.id == Assignment.course_id, Course.user_id == Assignment.user_id))
        .where(Assignment.user_id == auth.user.id, Course.archived_at.is_(None), due >= start, due < end)
        .order_by(deadline_sql(tz), Assignment.id)
    )
    return CalendarOut(
        start=start,
        end=end,
        time_zone=tz,
        occurrences=occurrences,
        deadlines=[to_out(a, tz, now) for a in deadlines],
    )


@router.get("/events/{event_id}", response_model=EventOut)
def get_event(event_id: int, auth: CurrentAuth, db: DB) -> EventOut:
    return event_out(_owned(db, auth.user.id, event_id), auth.user.time_zone)


@router.post(
    "/events",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ScheduleConflict}},
)
def create_event(
    body: EventFields, auth: CurrentAuth, db: DB, response: Response, key: IdempotencyKey = None
) -> EventOut | JSONResponse:
    user = auth.user
    if key is not None and (existing := _by_key(db, user.id, key)) is not None:
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
        return event_out(existing, user.time_zone)

    _check_course(db, user.id, body.course_id)
    cols, ambiguous = _columns(body, user.time_zone)
    if not body.allow_conflicts and (warning := _conflict_response(db, user, cols, None)) is not None:
        # The "conflict" may be this same request, saved a moment ago by a concurrent retry.
        if key is not None and (replay := _by_key(db, user.id, key)) is not None:
            response.status_code = status.HTTP_200_OK
            response.headers["Idempotent-Replayed"] = "true"
            return event_out(replay, user.time_zone)
        return warning

    new_id = db.scalar(
        insert(Event)
        .values(
            user_id=user.id,
            course_id=body.course_id,
            title=body.title,
            kind=body.kind,
            location=body.location,
            idempotency_key=key,
            **cols,
        )
        .on_conflict_do_nothing(constraint="uq_events_idempotency")
        .returning(Event.id)
    )
    db.commit()
    if new_id is None:  # a concurrent retry with the same key won the race
        response.status_code = status.HTTP_200_OK
        response.headers["Idempotent-Replayed"] = "true"
        assert key is not None
        replay = _by_key(db, user.id, key)
        assert replay is not None
        return event_out(replay, user.time_zone)
    return event_out(_owned(db, user.id, new_id), user.time_zone, ambiguous)


@router.put("/events/{event_id}", response_model=EventOut, responses={409: {"model": ScheduleConflict}})
def update_event(event_id: int, body: EventFields, auth: CurrentAuth, db: DB) -> EventOut | JSONResponse:
    """Edits the whole event. For a weekly class that means every meeting in the series."""
    user = auth.user
    e = _owned(db, user.id, event_id)
    _check_course(db, user.id, body.course_id, current=e.course_id)
    # A weekly class keeps the zone it was defined in, so editing its title from
    # another time zone doesn't silently move every meeting.
    tz = e.time_zone if (e.time_zone and isinstance(body.schedule, WeeklySchedule)) else user.time_zone
    cols, ambiguous = _columns(body, tz)
    if not body.allow_conflicts and (warning := _conflict_response(db, user, cols, e.id)) is not None:
        return warning
    for k, v in cols.items():
        setattr(e, k, v)
    e.title, e.kind, e.location, e.course_id = body.title, body.kind, body.location, body.course_id
    db.commit()
    db.refresh(e)
    return event_out(e, user.time_zone, ambiguous)


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(event_id: int, auth: CurrentAuth, db: DB) -> None:
    db.delete(_owned(db, auth.user.id, event_id))
    db.commit()
