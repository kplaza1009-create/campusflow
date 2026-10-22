"""The scheduling rules, tested directly (no database, no HTTP)."""

from datetime import UTC, date, datetime, time, timedelta

from app.scheduling import Occurrence, WeeklyRule, conflicts, expand_weekly, overlapping_pairs, overlaps

NY = "America/New_York"


def utc(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def occ(start: datetime, minutes: int, event_id: int = 1) -> Occurrence:
    return Occurrence(start, start + timedelta(minutes=minutes), event_id)


def rule(days: set[int], start: time, minutes: int, first: date, last: date, tz: str = NY) -> WeeklyRule:
    return WeeklyRule(frozenset(days), start, timedelta(minutes=minutes), tz, first, last)


# --- Overlap boundaries ----------------------------------------------------------


def test_adjacent_events_do_not_conflict() -> None:
    a = occ(utc(2026, 10, 19, 14), 60, 1)
    b = occ(utc(2026, 10, 19, 15), 60, 2)
    assert not overlaps(a, b) and not overlaps(b, a)


def test_one_minute_overlap_conflicts() -> None:
    assert overlaps(occ(utc(2026, 10, 19, 14), 61, 1), occ(utc(2026, 10, 19, 15), 60, 2))


def test_contained_and_identical_events_conflict() -> None:
    outer = occ(utc(2026, 10, 19, 14), 180, 1)
    assert overlaps(outer, occ(utc(2026, 10, 19, 15), 30, 2))
    assert overlaps(outer, occ(utc(2026, 10, 19, 14), 180, 2))


def test_overnight_event_conflicts_with_next_morning() -> None:
    # 23:00 to 01:00 New York (03:00 to 05:00 UTC) vs 00:30 to 01:30 the next day.
    late = occ(utc(2026, 10, 20, 3), 120, 1)
    early = occ(utc(2026, 10, 20, 4, 30), 60, 2)
    assert overlaps(late, early)


def test_overlapping_pairs_sweep() -> None:
    a = occ(utc(2026, 10, 19, 14), 60, 1)
    b = occ(utc(2026, 10, 19, 14, 30), 60, 2)
    c = occ(utc(2026, 10, 19, 15, 30), 30, 3)  # touches b's end: no conflict with b, none with a
    d = occ(utc(2026, 10, 19, 17), 60, 4)
    pairs = {(x.event_id, y.event_id) for x, y in overlapping_pairs([d, c, b, a])}
    assert pairs == {(1, 2)}


def test_conflicts_lists_each_overlap() -> None:
    cand = [occ(utc(2026, 10, 19, 14), 60, 9), occ(utc(2026, 10, 21, 14), 60, 9)]
    existing = [
        occ(utc(2026, 10, 19, 14, 30), 60, 1),
        occ(utc(2026, 10, 20, 14), 60, 2),
        occ(utc(2026, 10, 21, 15), 30, 3),
    ]
    found = [(c.start.day, e.event_id) for c, e in conflicts(cand, existing)]
    assert found == [(19, 1)]


# --- Weekly expansion ------------------------------------------------------------


def test_weekly_class_expands_only_inside_window_and_series() -> None:
    mwf = rule({0, 2, 4}, time(10), 50, date(2026, 9, 2), date(2026, 12, 11))  # starts on a Wednesday
    week = list(expand_weekly(1, mwf, utc(2026, 10, 19, 4), utc(2026, 10, 26, 4)))
    assert [o.start for o in week] == [utc(2026, 10, 19, 14), utc(2026, 10, 21, 14), utc(2026, 10, 23, 14)]
    first_week = list(expand_weekly(1, mwf, utc(2026, 8, 31, 4), utc(2026, 9, 7, 4)))
    assert [o.start.day for o in first_week] == [2, 4]  # nothing before the first date
    after = list(expand_weekly(1, mwf, utc(2026, 12, 14, 5), utc(2026, 12, 21, 5)))
    assert after == []  # nothing after the last date


def test_class_stays_at_local_time_through_dst() -> None:
    tue = rule({1}, time(9), 75, date(2026, 10, 1), date(2026, 11, 30))
    occs = list(expand_weekly(1, tue, utc(2026, 10, 26), utc(2026, 11, 11)))
    # 9:00 EDT (13:00 UTC) before Nov 1, 9:00 EST (14:00 UTC) after.
    assert [o.start for o in occs] == [utc(2026, 10, 27, 13), utc(2026, 11, 3, 14), utc(2026, 11, 10, 14)]
    assert all(o.end - o.start == timedelta(minutes=75) for o in occs)


def test_occurrence_in_spring_forward_gap_moves_forward() -> None:
    sun = rule({6}, time(2, 30), 60, date(2026, 3, 1), date(2026, 3, 31))
    occs = list(expand_weekly(1, sun, utc(2026, 3, 7), utc(2026, 3, 10)))
    assert occs[0].start == utc(2026, 3, 8, 7, 30)  # 3:30 EDT: the skipped 2:30 moved forward


def test_occurrence_in_fall_back_overlap_uses_first() -> None:
    sun = rule({6}, time(1, 30), 30, date(2026, 10, 1), date(2026, 11, 30))
    occs = list(expand_weekly(1, sun, utc(2026, 10, 31), utc(2026, 11, 3)))
    assert occs[0].start == utc(2026, 11, 1, 5, 30)  # 1:30 EDT, the first of the two


def test_overnight_class_from_before_window_is_included() -> None:
    sun_night = rule({6}, time(23), 120, date(2026, 10, 1), date(2026, 12, 31))
    # Monday's window starts at midnight local (04:00 UTC); Sunday's class runs until 01:00.
    occs = list(expand_weekly(1, sun_night, utc(2026, 10, 19, 4), utc(2026, 10, 20, 4)))
    assert [(o.start, o.end) for o in occs] == [(utc(2026, 10, 19, 3), utc(2026, 10, 19, 5))]


def test_class_defined_in_another_zone_keeps_its_own_wall_time() -> None:
    la = rule({0}, time(9), 60, date(2026, 10, 1), date(2026, 10, 31), tz="America/Los_Angeles")
    occs = list(expand_weekly(1, la, utc(2026, 10, 19), utc(2026, 10, 20, 23)))
    assert occs[0].start == utc(2026, 10, 19, 16)  # 9:00 PDT, which is 12:00 in New York
