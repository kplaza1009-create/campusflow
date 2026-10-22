"""One user can never read or change another user's records, however the request is
modified: different IDs in the URL, foreign IDs in the body, or direct SQL."""

import pytest
from conftest import Api
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import engine


@pytest.fixture
def victim_data(other: Api) -> dict[str, int]:
    course = other.course("Private Course")
    assignment = other.assignment(course["id"], "Private Essay")
    return {"course": course["id"], "assignment": assignment["id"]}


def test_lists_only_show_own_records(api: Api, victim_data: dict[str, int]) -> None:
    assert api.get("/api/courses?include_archived=true").json() == []
    assert api.get("/api/assignments").json() == []
    assert api.get(f"/api/assignments?course_id={victim_data['course']}").json() == []


def test_reading_others_assignment_by_id_returns_404(api: Api, victim_data: dict[str, int]) -> None:
    assert api.get(f"/api/assignments/{victim_data['assignment']}").status_code == 404


def test_modifying_others_records_by_id_returns_404(
    api: Api, other: Api, victim_data: dict[str, int]
) -> None:
    aid, cid = victim_data["assignment"], victim_data["course"]
    mine = api.course("Mine")
    body = {"title": "Hijacked", "course_id": mine["id"], "due_date": "2026-11-01"}
    assert api.put(f"/api/assignments/{aid}", body).status_code == 404
    assert api.post(f"/api/assignments/{aid}/completion", {"completed": True}).status_code == 404
    assert api.delete(f"/api/assignments/{aid}").status_code == 404
    assert api.patch(f"/api/courses/{cid}", {"name": "Hijacked", "archived": True}).status_code == 404
    assert api.get(f"/api/courses/{cid}/deletion-impact").status_code == 404
    assert api.delete(f"/api/courses/{cid}?expected_assignments=1").status_code == 404

    # Nothing changed for the owner.
    a = other.get(f"/api/assignments/{aid}").json()
    assert a["title"] == "Private Essay" and a["completed"] is False
    courses = other.get("/api/courses").json()
    assert [c["name"] for c in courses] == ["Private Course"]


def test_cannot_attach_assignment_to_another_users_course(api: Api, victim_data: dict[str, int]) -> None:
    body = {"title": "Sneaky", "course_id": victim_data["course"], "due_date": "2026-11-01"}
    assert api.post("/api/assignments", body).status_code == 404

    own_course = api.course("Mine")
    own = api.assignment(own_course["id"], "Legit")
    moved = {"title": "Legit", "course_id": victim_data["course"], "due_date": "2026-11-01"}
    assert api.put(f"/api/assignments/{own['id']}", moved).status_code == 404


def test_database_rejects_cross_user_course_reference(api: Api, victim_data: dict[str, int]) -> None:
    """Defense in depth: even a bug in the API couldn't link these, because the
    composite foreign key (user_id, course_id) must match a course of that same user."""
    alex_id = api.user["id"]  # type: ignore[index]
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO assignments (user_id, course_id, title, due_date) "
                "VALUES (:u, :c, 'x', '2026-11-01')"
            ),
            {"u": alex_id, "c": victim_data["course"]},
        )


def test_idempotency_keys_are_scoped_per_user(api: Api, other: Api) -> None:
    key = {"Idempotency-Key": "8a1c4f9e-6a7b-4f1e-9a3c-1b2c3d4e5f60"}
    theirs = other.post("/api/courses", {"name": "Theirs"}, headers=key)
    assert theirs.status_code == 201
    mine = api.post("/api/courses", {"name": "Mine"}, headers=key)
    assert mine.status_code == 201  # a new record, not a replay of the other user's
    assert mine.json()["name"] == "Mine"


def test_unauthenticated_requests_are_rejected(anon: Api) -> None:
    for path in ("/api/courses", "/api/assignments", "/api/today", "/api/assignments/1"):
        assert anon.get(path).status_code == 401
