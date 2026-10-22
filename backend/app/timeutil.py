"""Time zone rules shared by the API (and later the calendar and reminder worker).

Key decisions:
- A date-only deadline is a calendar date. It is overdue once that date has fully
  passed in the user's *current* time zone (i.e. after 11:59 PM local).
- A timed deadline is an exact instant stored in UTC. Changing the display time
  zone changes how it is shown, never when it is.
- When a user types a local wall-clock time, it is converted with their IANA zone.
  Times that don't exist (spring-forward gap) are rejected; ambiguous times
  (fall-back overlap) resolve to the earlier occurrence and are flagged.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones


@lru_cache
def _zones() -> frozenset[str]:
    return frozenset(available_timezones())


def is_valid_zone(name: str) -> bool:
    return name in _zones()


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:  # pragma: no cover - validated earlier
        raise ValueError(f"Unknown time zone: {name}") from exc


class NonexistentLocalTime(ValueError):
    """The wall-clock time is skipped by a daylight saving transition."""


@dataclass(frozen=True)
class LocalizedTime:
    instant: datetime  # aware, UTC
    ambiguous: bool  # True when the wall time occurred twice and we chose the earlier one


def localize(d: date, t: time, tz_name: str) -> LocalizedTime:
    """Convert a local date and wall-clock time in `tz_name` to a UTC instant."""
    tz = zone(tz_name)
    first = datetime.combine(d, t, tzinfo=tz).replace(fold=0)
    second = first.replace(fold=1)

    # A nonexistent time does not survive a round trip through UTC.
    round_trip = first.astimezone(UTC).astimezone(tz)
    if round_trip.replace(tzinfo=None) != first.replace(tzinfo=None):
        raise NonexistentLocalTime(
            f"{t.strftime('%H:%M')} does not exist on {d.isoformat()} in {tz_name} "
            "because clocks move forward for daylight saving time."
        )

    ambiguous = first.utcoffset() != second.utcoffset()
    return LocalizedTime(instant=first.astimezone(UTC), ambiguous=ambiguous)


def local_today(now: datetime, tz_name: str) -> date:
    return now.astimezone(zone(tz_name)).date()


def end_of_date(d: date, tz_name: str) -> datetime:
    """The instant a date-only deadline becomes overdue: local midnight after `d`."""
    next_midnight = datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=zone(tz_name))
    return next_midnight.astimezone(UTC)


def start_of_date(d: date, tz_name: str) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=zone(tz_name)).astimezone(UTC)
