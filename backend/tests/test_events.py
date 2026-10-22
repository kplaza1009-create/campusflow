"""Calendar events through the API: validation, conflicts, recurrence, time zones, isolation.

The clock (conftest) is Wed 2026-10-14 12:00 in New York. The user's zone is America/New_York.
"""

import uuid
from typing import Any

import pytest
from conftest import Api

WEEK = "2026-10-12"  # Monday


def once(date: str, start: str, end: str, end_date: str | None = None) -> dict[str, Any]:
    return {
        "type": "once",
        "start_date": date,
        "start_time": start,
        "end_date": end_date or date,
        "end_time": end,
    }


def weekly(
    days: list[int], start: str, end: str, first: str = "2026-09-02", last: str = "2026-12-11"
) -> dict[str, Any]:
    return {
        "type": "weekly",
        "weekdays": days,
        "start_time": start,
        "end_time": end,
        "first_date": first,
        "last_date": last,
    }


def make(api: Api, title: str, schedule: dict[str, Any], expect: int = 201, **fields: Any) -> Any:
    r = api.post("/api/events", {"title": title, "schedule": schedule, **fields})
    assert r.status_code == expect, r.text
    return r.json()


def week(api: Api, start: str = WEEK, days: int = 7) -> dict[str, Any]:
    r = api.get(f"/api/calendar?start={start}&days={days}")
    assert r.status_code == 200, r.text
    return dict(r.json())


# --- Validation ----------------------------------------------------------------


def test_validation(api: Api) -> None:
    base = {"title": "Study", "schedule": once("2026-10-15", "14:00", "15:00")}
    assert api.post("/api/events", base | {"title": " "}).status_code == 422
    assert api.post("/api/events", base | {"kind": "party"}).status_code == 422
    end_before = api.post("/api/events", base | {"schedule": once("2026-10-15", "15:00", "14:00")})
    assert end_before.status_code == 422 and "after the start" in end_before.json()["detail"]
    zero = api.post("/api/events", base | {"schedule": once("2026-10-15", "15:00", "15:00")})
    assert zero.status_code == 422
    too_long = api.post(
        "/api/events", base | {"schedule": once("2026-10-01", "09:00", "09:00", "2026-10-20")}
    )
    assert too_long.status_code == 422
    assert api.post("/api/events", base | {"schedule": weekly([], "09:00", "10:00")}).status_code == 422
    assert api.post("/api/events", base | {"schedule": weekly([7], "09:00", "10:00")}).status_code == 422
    assert api.post("/api/events", base | {"schedule": weekly([0], "09:00", "09:00")}).status_code == 422
    backwards = weekly([0], "09:00", "10:00", first="2026-12-01", last="2026-09-01")
    assert api.post("/api/events", base | {"schedule": backwards}).status_code == 422
    year_plus = weekly([0], "09:00", "10:00", first="2026-01-01", last="2027-06-01")
    assert api.post("/api/events", base | {"schedule": year_plus}).status_code == 422
    # A Saturday-only class between a Monday and a Wednesday never meets.
    never = api.post(
        "/api/events", base | {"schedule": weekly([5], "09:00", "10:00", "2026-10-12", "2026-10-14")}
    )
    assert never.status_code == 422 and "never meets" in never.json()["detail"]
    assert api.post("/api/events", base | {"course_id": 999999}).status_code == 404


def test_create_one_time_and_read_back(api: Api) -> None:
    course = api.course("Database Systems I", code="CS248")
    e = make(
        api,
        "Study group",
        once("2026-10-15", "14:00", "15:30"),
        kind="study",
        course_id=course["id"],
        location=" Library 3F ",
    )
    assert (e["kind"], e["location"], e["recurring"], e["course"]["code"]) == (
        "study",
        "Library 3F",
        False,
        "CS248",
    )
    assert e["schedule"] == once("2026-10-15", "14:00:00", "15:30:00")
    assert api.get(f"/api/events/{e['id']}").json() == e


# --- Conflicts -----------------------------------------------------------------


def test_overlap_is_warned_and_can_be_saved_anyway(api: Api) -> None:
    first = make(api, "Office hours", once("2026-10-15", "14:00", "15:00"))
    warn = api.post(
        "/api/events", {"title": "Group meeting", "schedule": once("2026-10-15", "14:30", "15:30")}
    )
    assert warn.status_code == 409
    body = warn.json()
    assert body["code"] == "schedule_conflict" and body["total"] == 1
    c = body["conflicts"][0]
    assert (c["event_id"], c["title"], c["start_time"], c["end_time"]) == (
        first["id"],
        "Office hours",
        "14:00:00",
        "15:00:00",
    )

    saved = make(api, "Group meeting", once("2026-10-15", "14:30", "15:30"), allow_conflicts=True)
    occs = {o["event_id"]: o for o in week(api)["occurrences"]}
    assert occs[first["id"]]["conflicts_with"] == [saved["id"]]
    assert occs[saved["id"]]["conflicts_with"] == [first["id"]]


def test_adjacent_events_do_not_conflict(api: Api) -> None:
    make(api, "Lecture", once("2026-10-15", "14:00", "15:00"))
    make(api, "Lab", once("2026-10-15", "15:00", "16:00"))  # 201, no warning
    assert all(o["conflicts_with"] == [] for o in week(api)["occurrences"])


def test_deadlines_never_conflict(api: Api) -> None:
    course = api.course("Calculus II")
    api.assignment(course["id"], "Problem set", "2026-10-15", due_time="14:30")
    make(api, "Study", once("2026-10-15", "14:00", "15:00"))  # no warning
    cal = week(api)
    assert [d["title"] for d in cal["deadlines"]] == ["Problem set"]
    assert cal["occurrences"][0]["conflicts_with"] == []


def test_recurring_class_conflicts_with_one_time_event(api: Api) -> None:
    cls = make(api, "Calculus II", weekly([0, 2, 4], "10:00", "10:50"), kind="class")
    warn = api.post(
        "/api/events", {"title": "Dentist", "schedule": once("2026-10-21", "10:30", "11:30")}
    )  # a Wednesday
    assert warn.status_code == 409
    c = warn.json()["conflicts"][0]
    assert (c["event_id"], c["start_date"], c["start_time"]) == (cls["id"], "2026-10-21", "10:00:00")
    make(api, "Dentist", once("2026-10-20", "10:30", "11:30"))  # a Tuesday: free


def test_new_weekly_class_lists_every_clashing_meeting(api: Api) -> None:
    make(api, "Chemistry", weekly([1, 3], "13:00", "14:15", "2026-09-01", "2026-12-10"), kind="class")
    warn = api.post(
        "/api/events",
        {
            "title": "Lab",
            "kind": "class",
            "schedule": weekly([3], "14:00", "16:00", "2026-10-01", "2026-10-31"),
        },
    )
    assert warn.status_code == 409
    # Every Thursday in October 2026: 1, 8, 15, 22, 29.
    assert warn.json()["total"] == 5
    assert [c["start_date"] for c in warn.json()["conflicts"]] == [
        "2026-10-01",
        "2026-10-08",
        "2026-10-15",
        "2026-10-22",
        "2026-10-29",
    ]


def test_weekly_classes_in_different_terms_do_not_conflict(api: Api) -> None:
    make(api, "Fall seminar", weekly([0], "09:00", "10:00", "2026-09-01", "2026-12-15"))
    make(api, "Spring seminar", weekly([0], "09:00", "10:00", "2027-01-20", "2027-05-10"))


def test_overnight_event_conflicts_across_midnight(api: Api) -> None:
    late = make(api, "Hackathon shift", once("2026-10-15", "22:00", "02:00", end_date="2026-10-16"))
    warn = api.post("/api/events", {"title": "Early study", "schedule": once("2026-10-16", "01:00", "03:00")})
    assert warn.status_code == 409 and warn.json()["conflicts"][0]["event_id"] == late["id"]
    cal = week(api)
    occ = cal["occurrences"][0]
    assert (occ["start_date"], occ["start_time"], occ["end_date"], occ["end_time"]) == (
        "2026-10-15",
        "22:00:00",
        "2026-10-16",
        "02:00:00",
    )


def test_overnight_weekly_class(api: Api) -> None:
    e = make(api, "Night lab", weekly([6], "23:00", "01:00", "2026-10-01", "2026-10-31"))  # Sundays
    assert e["ends_next_day"] is True and e["schedule"]["end_time"] == "01:00:00"
    # The Sunday 23:00 meeting reaches into Monday the 19th.
    monday = week(api, "2026-10-19", 1)["occurrences"]
    assert [(o["start_date"], o["end_date"], o["end_time"]) for o in monday] == [
        ("2026-10-18", "2026-10-19", "01:00:00")
    ]


def test_editing_a_class_updates_the_whole_series_and_ignores_itself(api: Api) -> None:
    e = make(api, "Physics", weekly([1, 3], "11:00", "12:15"), kind="class")
    # Moving it later in the day shouldn't "conflict" with its own old times.
    r = api.put(
        f"/api/events/{e['id']}",
        {"title": "Physics I", "kind": "class", "schedule": weekly([1, 3], "11:30", "12:45")},
    )
    assert r.status_code == 200, r.text
    starts = {o["start_time"] for o in week(api)["occurrences"]}
    assert starts == {"11:30:00"}
    assert {o["title"] for o in week(api)["occurrences"]} == {"Physics I"}


def test_edit_can_also_conflict_and_be_forced(api: Api) -> None:
    make(api, "Club", once("2026-10-15", "18:00", "19:00"))
    e = make(api, "Call", once("2026-10-15", "20:00", "21:00"))
    moved = {"title": "Call", "schedule": once("2026-10-15", "18:30", "19:30")}
    assert api.put(f"/api/events/{e['id']}", moved).status_code == 409
    assert api.put(f"/api/events/{e['id']}", moved | {"allow_conflicts": True}).status_code == 200


def test_delete_event(api: Api) -> None:
    e = make(api, "Temp", once("2026-10-15", "18:00", "19:00"))
    assert api.delete(f"/api/events/{e['id']}").status_code == 204
    assert api.get(f"/api/events/{e['id']}").status_code == 404
    assert week(api)["occurrences"] == []


def test_idempotent_create(api: Api) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    body = {"title": "Review", "schedule": once("2026-10-16", "10:00", "11:00")}
    first = api.post("/api/events", body, headers=key)
    again = api.post("/api/events", body, headers=key)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"]
    assert len(week(api)["occurrences"]) == 1


# --- Calendar window -----------------------------------------------------------


def test_calendar_window_is_bounded(api: Api) -> None:
    make(api, "MWF", weekly([0, 2, 4], "10:00", "10:50"))
    assert len(week(api)["occurrences"]) == 3
    assert len(week(api, "2026-10-12", 14)["occurrences"]) == 6
    assert len(week(api, "2026-10-14", 1)["occurrences"]) == 1
    assert api.get("/api/calendar?start=2026-10-12&days=0").status_code == 422
    assert api.get("/api/calendar?start=2026-10-12&days=43").status_code == 422


def test_calendar_deadlines_and_archived_courses(api: Api) -> None:
    keep = api.course("Keep")
    old = api.course("Old")
    api.assignment(keep["id"], "Due Friday", "2026-10-16")
    api.assignment(keep["id"], "Next month", "2026-11-20")
    make(api, "Old seminar", weekly([0], "09:00", "10:00"), course_id=old["id"])
    api.patch(f"/api/courses/{old['id']}", {"name": "Old", "archived": True})
    cal = week(api)
    assert [d["title"] for d in cal["deadlines"]] == ["Due Friday"]
    assert cal["occurrences"] == []  # archived courses' events are hidden
    # ...and don't produce conflicts either.
    make(api, "New", weekly([0], "09:00", "10:00"))


def test_today_includes_todays_events(api: Api) -> None:
    make(api, "Lecture", weekly([2], "10:00", "10:50"))  # Wednesdays; today is Wed Oct 14
    make(api, "Tomorrow thing", once("2026-10-15", "10:00", "11:00"))
    today = api.get("/api/today").json()
    assert [e["title"] for e in today["events"]] == ["Lecture"]


# --- Time zones -------------------------------------------------------------------


def test_zone_change_keeps_one_time_instant_and_class_wall_time(api: Api) -> None:
    one = make(api, "Interview", once("2026-10-15", "15:00", "16:00"))
    cls = make(api, "Seminar", weekly([3], "09:00", "10:00"))  # Thursdays, defined in New York time
    api.patch("/api/account", {"time_zone": "America/Los_Angeles"})

    occs = {o["event_id"]: o for o in week(api)["occurrences"]}
    assert occs[one["id"]]["start"] == "2026-10-15T19:00:00Z"  # same instant
    assert occs[one["id"]]["start_time"] == "12:00:00"  # shown in Pacific time
    assert occs[cls["id"]]["start_time"] == "06:00:00"  # 9:00 New York, shown in Pacific time

    # The class keeps its own zone when edited from Los Angeles.
    r = api.put(
        f"/api/events/{cls['id']}", {"title": "Seminar II", "schedule": weekly([3], "09:00", "10:00")}
    )
    assert r.json()["time_zone"] == "America/New_York"
    assert {o["start_time"] for o in week(api)["occurrences"] if o["event_id"] == cls["id"]} == {"06:00:00"}


def test_class_keeps_local_time_across_dst_in_calendar(api: Api) -> None:
    make(api, "Seminar", weekly([4], "09:00", "10:00"))  # Fridays
    before = week(api, "2026-10-26")["occurrences"][0]
    after = week(api, "2026-11-02")["occurrences"][0]
    assert (before["start_time"], after["start_time"]) == ("09:00:00", "09:00:00")
    assert (before["start"], after["start"]) == ("2026-10-30T13:00:00Z", "2026-11-06T14:00:00Z")


def test_nonexistent_one_time_start_is_rejected(api: Api) -> None:
    r = api.post("/api/events", {"title": "Gap", "schedule": once("2026-03-08", "02:30", "04:00")})
    assert r.status_code == 422 and "daylight saving" in r.json()["detail"]


# --- Ownership -------------------------------------------------------------------


@pytest.fixture
def victim_event(other: Api) -> dict[str, Any]:
    c = other.course("Private")
    return dict(
        make(other, "Private meeting", once("2026-10-15", "14:00", "15:00"), course_id=c["id"])
        | {"course_id": c["id"]}
    )


def test_other_users_events_are_invisible_and_untouchable(api: Api, victim_event: dict[str, Any]) -> None:
    eid = victim_event["id"]
    assert api.get(f"/api/events/{eid}").status_code == 404
    assert (
        api.put(
            f"/api/events/{eid}", {"title": "x", "schedule": once("2026-10-15", "14:00", "15:00")}
        ).status_code
        == 404
    )
    assert api.delete(f"/api/events/{eid}").status_code == 404
    assert week(api)["occurrences"] == []
    # Their event doesn't cause conflicts for me, and I can't use their course.
    make(api, "Mine", once("2026-10-15", "14:00", "15:00"))
    body = {
        "title": "Sneaky",
        "course_id": victim_event["course_id"],
        "schedule": once("2026-10-16", "14:00", "15:00"),
    }
    assert api.post("/api/events", body).status_code == 404


def test_deleting_a_course_counts_and_removes_its_events(api: Api) -> None:
    c = api.course("Biology")
    make(api, "Bio lecture", weekly([0, 2], "13:00", "14:00"), course_id=c["id"])
    make(api, "Free study", once("2026-10-15", "09:00", "10:00"))
    assert api.get(f"/api/courses/{c['id']}/deletion-impact").json() == {"assignments": 0, "events": 1}
    assert api.delete(f"/api/courses/{c['id']}?expected_assignments=0").status_code == 409
    assert api.delete(f"/api/courses/{c['id']}?expected_assignments=0&expected_events=1").status_code == 204
    assert [o["title"] for o in week(api)["occurrences"]] == ["Free study"]
