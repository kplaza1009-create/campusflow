"""Scheduling rules: turning weekly classes into occurrences and finding overlaps.

Pure functions with no database or web code, so the rules are tested directly
(tests/test_scheduling.py). The API layer loads rows, calls these, and shapes
the results.

Rules:
- An event occupies [start, end). Adjacent events (one ends 10:00, the next
  starts 10:00) do not conflict. Assignment deadlines never occupy time.
- A weekly class is defined by local wall-clock time in its own IANA zone, so a
  9:00 class stays at 9:00 local through daylight saving changes; its UTC time
  shifts instead.
- If a class's start time doesn't exist on some date (clocks jump forward),
  that occurrence moves forward by the gap (2:30 becomes 3:30). If it happens
  twice (clocks fall back), the first one is used.
- A class lasts a fixed elapsed duration, so overnight classes (22:00 to 01:00)
  simply end on the next day.
- Expansion is always bounded by a window, so no query walks a whole semester
  unless asked to.
"""

from bisect import bisect_left
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app.timeutil import zone


@dataclass(frozen=True)
class WeeklyRule:
    weekdays: frozenset[int]  # 0 = Monday ... 6 = Sunday
    start_time: time
    duration: timedelta
    time_zone: str
    first_date: date
    last_date: date


@dataclass(frozen=True, order=True)
class Occurrence:
    start: datetime  # UTC
    end: datetime  # UTC, exclusive
    event_id: int


def local_start(d: date, t: time, tz_name: str) -> datetime:
    """The UTC instant of wall time `t` on date `d` in `tz_name`.

    zoneinfo with fold=0 gives exactly the documented policy: a skipped time is
    pushed forward by the gap, and a repeated time resolves to the first one.
    """
    return datetime.combine(d, t, tzinfo=zone(tz_name)).astimezone(UTC)


def expand_weekly(
    event_id: int, rule: WeeklyRule, window_start: datetime, window_end: datetime
) -> Iterator[Occurrence]:
    """Occurrences of a weekly class that overlap [window_start, window_end)."""
    tz = zone(rule.time_zone)
    # An occurrence that starts before the window can still run into it (overnight),
    # so begin one duration earlier, plus a day of slack for UTC offsets.
    first = max(rule.first_date, (window_start - rule.duration).astimezone(tz).date() - timedelta(days=1))
    last = min(rule.last_date, window_end.astimezone(tz).date())
    d = first
    while d <= last:
        if d.weekday() in rule.weekdays:
            start = local_start(d, rule.start_time, rule.time_zone)
            end = start + rule.duration
            if start < window_end and end > window_start:
                yield Occurrence(start, end, event_id)
        d += timedelta(days=1)


def overlaps(a: Occurrence, b: Occurrence) -> bool:
    return a.start < b.end and b.start < a.end


def overlapping_pairs(occurrences: list[Occurrence]) -> list[tuple[Occurrence, Occurrence]]:
    """Every overlapping pair, via a sweep over start times: O(n log n + pairs)."""
    pairs: list[tuple[Occurrence, Occurrence]] = []
    active: list[Occurrence] = []
    for occ in sorted(occurrences):
        active = [a for a in active if a.end > occ.start]
        pairs.extend((a, occ) for a in active if a.event_id != occ.event_id)
        active.append(occ)
    return pairs


def conflicts(
    candidates: list[Occurrence], existing: list[Occurrence]
) -> list[tuple[Occurrence, Occurrence]]:
    """Pairs (candidate, existing) that overlap. `existing` must not contain the candidate's own event."""
    existing = sorted(existing)
    starts = [e.start for e in existing]
    found: list[tuple[Occurrence, Occurrence]] = []
    for c in sorted(candidates):
        # Only existing occurrences that start before the candidate ends can overlap it.
        for e in existing[: bisect_left(starts, c.end)]:
            if e.end > c.start:
                found.append((c, e))
    return found
