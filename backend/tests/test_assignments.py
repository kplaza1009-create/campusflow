"""Assignment validation, lifecycle, filtering, sorting, and duplicate protection."""

import threading
import uuid
from typing import Any

import pytest
from conftest import Api, Clock


@pytest.fixture
def course(api: Api) -> dict[str, Any]:
    return api.course("Database Systems I", code="CS248")


def test_validation(api: Api, course: dict[str, Any]) -> None:
    base = {"title": "HW", "course_id": course["id"], "due_date": "2026-10-20"}
    assert api.post("/api/assignments", base | {"title": "  "}).status_code == 422
    assert api.post("/api/assignments", base | {"title": "x" * 201}).status_code == 422
    assert api.post("/api/assignments", base | {"due_date": "not-a-date"}).status_code == 422
    assert api.post("/api/assignments", base | {"due_date": "1999-01-01"}).status_code == 422
    assert api.post("/api/assignments", base | {"due_time": "25:00"}).status_code == 422
    assert api.post("/api/assignments", base | {"reminder_minutes": 7}).status_code == 422
    assert api.post("/api/assignments", base | {"course_id": 999999}).status_code == 404
    ok = api.post(
        "/api/assignments", base | {"title": "  HW 1  ", "description": "  ", "reminder_minutes": None}
    )
    assert ok.status_code == 201
    assert (ok.json()["title"], ok.json()["description"], ok.json()["reminder_minutes"]) == (
        "HW 1",
        None,
        None,
    )


def test_lifecycle_edit_complete_reopen_delete(api: Api, course: dict[str, Any], clock: Clock) -> None:
    a = api.assignment(course["id"], "Essay draft", "2026-10-20")
    assert a["all_day"] is True and a["completed"] is False

    edited = api.put(
        f"/api/assignments/{a['id']}",
        {"title": "Essay final", "course_id": course["id"], "due_date": "2026-10-21", "due_time": "17:00"},
    ).json()
    assert edited["title"] == "Essay final"
    assert (edited["due_date"], edited["due_time"], edited["all_day"]) == ("2026-10-21", "17:00:00", False)
    assert edited["due_at"] == "2026-10-21T21:00:00Z"  # 5 PM EDT = 21:00 UTC

    done = api.post(f"/api/assignments/{a['id']}/completion", {"completed": True}).json()
    assert done["completed"] is True and done["completed_at"] is not None
    reopened = api.post(f"/api/assignments/{a['id']}/completion", {"completed": False}).json()
    assert reopened["completed"] is False and reopened["completed_at"] is None

    assert api.delete(f"/api/assignments/{a['id']}").status_code == 204
    assert api.get(f"/api/assignments/{a['id']}").status_code == 404


def _titles(api: Api, query: str = "") -> list[str]:
    r = api.get(f"/api/assignments{query}")
    assert r.status_code == 200, r.text
    return [a["title"] for a in r.json()]


def test_filters_search_and_sort(api: Api, course: dict[str, Any], clock: Clock) -> None:
    # Now: Wed 2026-10-14 12:00 in New York.
    other = api.course("Calculus II")
    api.assignment(course["id"], "ER diagram", "2026-10-10")  # overdue
    api.assignment(course["id"], "SQL joins lab", "2026-10-14", due_time="09:00")  # overdue (this morning)
    api.assignment(course["id"], "Normalization quiz", "2026-10-14")  # due today, not overdue
    done = api.assignment(other["id"], "Limits worksheet", "2026-10-12")
    api.post(f"/api/assignments/{done['id']}/completion", {"completed": True})
    api.assignment(other["id"], "100% effort problem set", "2026-10-30")

    assert _titles(api) == [
        "ER diagram",
        "Limits worksheet",
        "SQL joins lab",
        "Normalization quiz",
        "100% effort problem set",
    ]
    assert _titles(api, "?sort=due_desc")[0] == "100% effort problem set"
    assert _titles(api, f"?course_id={other['id']}") == ["Limits worksheet", "100% effort problem set"]
    assert _titles(api, "?status=completed") == ["Limits worksheet"]
    assert "Limits worksheet" not in _titles(api, "?status=open")
    assert _titles(api, "?overdue=true") == ["ER diagram", "SQL joins lab"]
    assert "ER diagram" not in _titles(api, "?overdue=false")
    assert _titles(api, "?due_from=2026-10-12&due_to=2026-10-14") == [
        "Limits worksheet",
        "SQL joins lab",
        "Normalization quiz",
    ]
    assert _titles(api, "?q=sql") == ["SQL joins lab"]
    assert _titles(api, "?q=QUIZ") == ["Normalization quiz"]
    # Wildcard characters are matched literally.
    assert _titles(api, "?q=100%25") == ["100% effort problem set"]
    assert _titles(api, "?q=_") == []
    assert api.get("/api/assignments?status=bogus").status_code == 422


def test_today_view_groups(api: Api, course: dict[str, Any], clock: Clock) -> None:
    api.assignment(course["id"], "Overdue", "2026-10-13")
    api.assignment(course["id"], "Today all-day", "2026-10-14")
    api.assignment(course["id"], "Today 11pm", "2026-10-14", due_time="23:00")
    api.assignment(course["id"], "Next week", "2026-10-21")
    api.assignment(course["id"], "Far away", "2026-11-30")
    t = api.get("/api/today").json()
    assert t["today"] == "2026-10-14"
    assert [a["title"] for a in t["overdue"]] == ["Overdue"]
    assert [a["title"] for a in t["due_today"]] == ["Today 11pm", "Today all-day"]
    assert [a["title"] for a in t["upcoming"]] == ["Next week"]


def test_idempotent_create_returns_original(api: Api, course: dict[str, Any]) -> None:
    key = {"Idempotency-Key": str(uuid.uuid4())}
    body = {"title": "Project proposal", "course_id": course["id"], "due_date": "2026-10-25"}
    first = api.post("/api/assignments", body, headers=key)
    retry = api.post("/api/assignments", body, headers=key)
    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json()["id"] == first.json()["id"]
    assert len(_titles(api)) == 1
    assert api.post("/api/assignments", body, headers={"Idempotency-Key": "nope"}).status_code == 422


def test_concurrent_double_submit_creates_one_record(api: Api, course: dict[str, Any]) -> None:
    """Simulates a double-click: several identical requests race with the same key."""
    key = {"Idempotency-Key": str(uuid.uuid4())}
    body = {"title": "Race", "course_id": course["id"], "due_date": "2026-10-25"}
    clients = [Api() for _ in range(6)]
    for c in clients:
        c.client.cookies = api.client.cookies
        c.csrf = api.csrf
    results: list[int] = []
    barrier = threading.Barrier(len(clients))

    def submit(c: Api) -> None:
        barrier.wait()
        r = c.post("/api/assignments", body, headers=key)
        results.append(r.status_code)

    threads = [threading.Thread(target=submit, args=(c,)) for c in clients]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results).count(201) == 1
    assert all(code in (200, 201) for code in results)
    assert _titles(api) == ["Race"]


def test_likely_duplicate_warning_and_save_anyway(api: Api, course: dict[str, Any]) -> None:
    api.assignment(course["id"], "Problem Set 3", "2026-10-22")
    body = {"title": "  problem set 3 ", "course_id": course["id"], "due_date": "2026-10-22"}
    warn = api.post("/api/assignments", body)
    assert warn.status_code == 409
    assert warn.json()["code"] == "likely_duplicate"
    assert [m["title"] for m in warn.json()["matches"]] == ["Problem Set 3"]

    saved = api.post("/api/assignments", body | {"allow_duplicate": True})
    assert saved.status_code == 201
    assert len(_titles(api)) == 2


def test_duplicate_rule_needs_all_three_to_match(api: Api, course: dict[str, Any]) -> None:
    other = api.course("Calculus II")
    api.assignment(course["id"], "Quiz", "2026-10-22")
    api.assignment(course["id"], "Quiz", "2026-10-23")  # different date
    api.assignment(other["id"], "Quiz", "2026-10-22")  # different course
    api.assignment(course["id"], "Quiz 2", "2026-10-22")  # different title


def test_duplicate_rule_compares_timed_deadline_by_local_date(api: Api, course: dict[str, Any]) -> None:
    api.assignment(course["id"], "Lab report", "2026-10-22", due_time="23:30")
    r = api.post(
        "/api/assignments", {"title": "Lab report", "course_id": course["id"], "due_date": "2026-10-22"}
    )
    assert r.status_code == 409


def test_editing_checks_duplicates_but_not_against_itself(api: Api, course: dict[str, Any]) -> None:
    a = api.assignment(course["id"], "Reading 1", "2026-10-22")
    b = api.assignment(course["id"], "Reading 2", "2026-10-22")
    same = {
        "title": "Reading 1",
        "course_id": course["id"],
        "due_date": "2026-10-22",
        "description": "pp. 1-20",
    }
    assert api.put(f"/api/assignments/{a['id']}", same).status_code == 200
    clash = api.put(f"/api/assignments/{b['id']}", same)
    assert clash.status_code == 409
    assert api.put(f"/api/assignments/{b['id']}", same | {"allow_duplicate": True}).status_code == 200
