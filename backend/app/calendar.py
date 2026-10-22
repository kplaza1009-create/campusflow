"""Loading a user's events for a time window and shaping their occurrences.

Shared by the events API (conflict checks, the week view) and the Today view.
The rules themselves live in app/scheduling.py.
"""

from datetime import datetime, time, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import Course, Event
from app.scheduling import Occurrence, WeeklyRule, expand_weekly, overlapping_pairs
from app.schemas import CourseBrief, OccurrenceOut
from app.timeutil import zone


def course_brief(c: Course | None) -> CourseBrief | None:
    if c is None:
        return None
    return CourseBrief(id=c.id, name=c.name, code=c.code, color=c.color, archived=c.archived_at is not None)


def rule_of(e: Event) -> WeeklyRule:
    assert e.weekdays is not None and e.start_time is not None and e.duration_minutes is not None
    assert e.time_zone is not None and e.first_date is not None and e.last_date is not None
    return WeeklyRule(
        weekdays=frozenset(e.weekdays),
        start_time=e.start_time,
        duration=timedelta(minutes=e.duration_minutes),
        time_zone=e.time_zone,
        first_date=e.first_date,
        last_date=e.last_date,
    )


def occurrences_of(e: Event, window_start: datetime, window_end: datetime) -> list[Occurrence]:
    if e.weekdays is None:
        assert e.starts_at is not None and e.ends_at is not None
        if e.starts_at < window_end and e.ends_at > window_start:
            return [Occurrence(e.starts_at, e.ends_at, e.id)]
        return []
    return list(expand_weekly(e.id, rule_of(e), window_start, window_end))


def load_events(
    db: Session, user_id: int, window_start: datetime, window_end: datetime, exclude_id: int | None = None
) -> list[Event]:
    """Events that may have an occurrence in the window. Archived courses' events are left out.

    One-time events are matched exactly. Weekly series are matched by their date
    range with two days of slack, since their dates are local to their own zone.
    """
    stmt = (
        select(Event)
        .outerjoin(Course, and_(Course.id == Event.course_id, Course.user_id == Event.user_id))
        .where(
            Event.user_id == user_id,
            Course.archived_at.is_(None),  # also true for events without a course
            or_(
                and_(Event.starts_at < window_end, Event.ends_at > window_start),
                and_(
                    Event.first_date <= window_end.date() + timedelta(days=1),
                    Event.last_date >= window_start.date() - timedelta(days=2),
                ),
            ),
        )
    )
    if exclude_id is not None:
        stmt = stmt.where(Event.id != exclude_id)
    return list(db.scalars(stmt).unique())


def window_occurrences(
    db: Session, user_id: int, window_start: datetime, window_end: datetime, exclude_id: int | None = None
) -> tuple[dict[int, Event], list[Occurrence]]:
    events = {e.id: e for e in load_events(db, user_id, window_start, window_end, exclude_id)}
    occs = [o for e in events.values() for o in occurrences_of(e, window_start, window_end)]
    return events, sorted(occs)


def occurrence_out(
    e: Event, occ: Occurrence, tz: str, conflicts_with: list[int] | None = None
) -> OccurrenceOut:
    start, end = occ.start.astimezone(zone(tz)), occ.end.astimezone(zone(tz))
    return OccurrenceOut(
        event_id=e.id,
        title=e.title,
        kind=e.kind,
        course=course_brief(e.course),
        location=e.location,
        recurring=e.recurring,
        start=occ.start,
        end=occ.end,
        start_date=start.date(),
        start_time=start.time().replace(tzinfo=None),
        end_date=end.date(),
        end_time=end.time().replace(tzinfo=None),
        conflicts_with=conflicts_with or [],
    )


def shaped_window(
    db: Session, user_id: int, tz: str, window_start: datetime, window_end: datetime
) -> list[OccurrenceOut]:
    """Occurrences in the window, each listing the other events it overlaps."""
    events, occs = window_occurrences(db, user_id, window_start, window_end)
    clashes: dict[Occurrence, set[int]] = {}
    for a, b in overlapping_pairs(occs):
        clashes.setdefault(a, set()).add(b.event_id)
        clashes.setdefault(b, set()).add(a.event_id)
    return [occurrence_out(events[o.event_id], o, tz, sorted(clashes.get(o, set()))) for o in occs]


def wall_end(start: time, minutes: int) -> tuple[time, bool]:
    """End wall-clock time of a class and whether it lands on the next day."""
    total = start.hour * 60 + start.minute + minutes
    return time((total // 60) % 24, total % 60), total >= 24 * 60
