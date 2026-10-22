"""Date-only deadlines, time zone changes, and daylight saving transitions."""

from datetime import UTC, date, datetime, time
from typing import Any

import pytest
from conftest import Api, Clock

from app.timeutil import NonexistentLocalTime, end_of_date, localize

# --- Pure functions ---------------------------------------------------------------


def test_localize_regular_time() -> None:
    r = localize(date(2026, 10, 14), time(17, 0), "America/New_York")
    assert r.instant == datetime(2026, 10, 14, 21, 0, tzinfo=UTC)
    assert r.ambiguous is False


def test_localize_rejects_spring_forward_gap() -> None:
    # 2:30 AM doesn't happen on 2026-03-08 in New York: clocks jump 2:00 -> 3:00.
    with pytest.raises(NonexistentLocalTime):
        localize(date(2026, 3, 8), time(2, 30), "America/New_York")


def test_localize_fall_back_overlap_picks_earlier() -> None:
    # 1:30 AM happens twice on 2026-11-01 in New York (EDT, then EST).
    r = localize(date(2026, 11, 1), time(1, 30), "America/New_York")
    assert r.ambiguous is True
    assert r.instant == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)  # 1:30 EDT, the first one


def test_end_of_date_across_dst() -> None:
    # The day clocks fall back is 25 hours long; the deadline is still local midnight.
    assert end_of_date(date(2026, 11, 1), "America/New_York") == datetime(2026, 11, 2, 5, 0, tzinfo=UTC)
    assert end_of_date(date(2026, 3, 8), "America/New_York") == datetime(2026, 3, 9, 4, 0, tzinfo=UTC)


# --- Through the API --------------------------------------------------------------


@pytest.fixture
def course(api: Api) -> dict[str, Any]:
    return api.course("History")


def _get(api: Api, aid: int) -> dict[str, Any]:
    return dict(api.get(f"/api/assignments/{aid}").json())


def test_date_only_overdue_exactly_after_1159_pm_local(
    api: Api, course: dict[str, Any], clock: Clock
) -> None:
    a = api.assignment(course["id"], "Reading", "2026-10-14")
    clock.set(datetime(2026, 10, 15, 3, 59, 59, tzinfo=UTC))  # 11:59:59 PM EDT on the 14th
    assert _get(api, a["id"])["overdue"] is False
    clock.set(datetime(2026, 10, 15, 4, 0, 0, tzinfo=UTC))  # midnight EDT, the 15th
    assert _get(api, a["id"])["overdue"] is True
    assert [x["title"] for x in api.get("/api/assignments?overdue=true").json()] == ["Reading"]


def test_completed_work_is_never_overdue(api: Api, course: dict[str, Any], clock: Clock) -> None:
    a = api.assignment(course["id"], "Done early", "2026-10-01")
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": True})
    assert _get(api, a["id"])["overdue"] is False
    assert api.get("/api/assignments?overdue=true").json() == []


def test_changing_time_zone_keeps_dates_and_instants(api: Api, course: dict[str, Any]) -> None:
    dated = api.assignment(course["id"], "Date only", "2026-10-20")
    timed = api.assignment(course["id"], "Timed", "2026-10-20", due_time="09:00")
    assert timed["due_at"] == "2026-10-20T13:00:00Z"

    assert api.patch("/api/account", {"time_zone": "America/Los_Angeles"}).status_code == 200

    d, t = _get(api, dated["id"]), _get(api, timed["id"])
    assert d["due_date"] == "2026-10-20"  # the date itself never moves
    assert d["deadline"] == "2026-10-21T07:00:00Z"  # but it now ends at Pacific midnight
    assert t["due_at"] == "2026-10-20T13:00:00Z"  # the exact instant is unchanged
    assert (t["due_date"], t["due_time"]) == ("2026-10-20", "06:00:00")  # shown in Pacific time


def test_time_zone_change_moves_timed_deadline_to_another_local_date(
    api: Api, course: dict[str, Any]
) -> None:
    timed = api.assignment(course["id"], "Late night", "2026-10-20", due_time="23:30")
    api.patch("/api/account", {"time_zone": "Europe/London"})
    t = _get(api, timed["id"])
    assert (t["due_date"], t["due_time"]) == ("2026-10-21", "04:30:00")


def test_overdue_follows_current_zone_for_date_only(api: Api, course: dict[str, Any], clock: Clock) -> None:
    a = api.assignment(course["id"], "Zone sensitive", "2026-10-14")
    clock.set(datetime(2026, 10, 15, 5, 0, tzinfo=UTC))  # 1 AM in New York, 10 PM in Los Angeles
    assert _get(api, a["id"])["overdue"] is True
    api.patch("/api/account", {"time_zone": "America/Los_Angeles"})
    assert _get(api, a["id"])["overdue"] is False


def test_invalid_zone_rejected(api: Api) -> None:
    assert api.patch("/api/account", {"time_zone": "EST5EDT-ish"}).status_code == 422
    assert api.patch("/api/account", {"time_zone": ""}).status_code == 422


def test_nonexistent_time_is_rejected_with_explanation(api: Api, course: dict[str, Any]) -> None:
    r = api.post(
        "/api/assignments",
        {"title": "Gap", "course_id": course["id"], "due_date": "2026-03-08", "due_time": "02:30"},
    )
    assert r.status_code == 422
    assert "daylight saving" in r.json()["detail"]


def test_ambiguous_time_is_flagged(api: Api, course: dict[str, Any]) -> None:
    r = api.post(
        "/api/assignments",
        {"title": "Overlap", "course_id": course["id"], "due_date": "2026-11-01", "due_time": "01:30"},
    )
    assert r.status_code == 201
    assert r.json()["time_note"] == "ambiguous_earlier"
    assert r.json()["due_at"] == "2026-11-01T05:30:00Z"


def test_today_uses_local_date_not_utc_date(api: Api, course: dict[str, Any], clock: Clock) -> None:
    clock.set(datetime(2026, 10, 15, 2, 0, tzinfo=UTC))  # still 10 PM on the 14th in New York
    api.assignment(course["id"], "Tonight", "2026-10-14")
    t = api.get("/api/today").json()
    assert t["today"] == "2026-10-14"
    assert [a["title"] for a in t["due_today"]] == ["Tonight"]
