"""Course validation, archiving, and deletion with confirmation."""

from conftest import Api


def test_course_validation(api: Api) -> None:
    assert api.post("/api/courses", {"name": "   "}).status_code == 422
    assert api.post("/api/courses", {"name": "x" * 121}).status_code == 422
    assert api.post("/api/courses", {"name": "Art", "color": "#123456"}).status_code == 422
    c = api.course("  Calculus II ", code=" MTH 202 ", instructor="", color="#0D9488")
    assert (c["name"], c["code"], c["instructor"], c["color"]) == ("Calculus II", "MTH 202", None, "#0d9488")


def test_course_create_is_idempotent(api: Api) -> None:
    key = {"Idempotency-Key": "0b9c6e2a-1d4f-4b8e-8c3a-5f6e7d8c9b0a"}
    first = api.post("/api/courses", {"name": "Physics"}, headers=key)
    again = api.post("/api/courses", {"name": "Physics"}, headers=key)
    assert first.status_code == 201 and again.status_code == 200
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json()["id"] == first.json()["id"]
    assert len(api.get("/api/courses").json()) == 1


def test_archive_preserves_assignments_and_hides_course(api: Api) -> None:
    course = api.course("Old Seminar")
    a = api.assignment(course["id"], "Final paper")
    r = api.patch(f"/api/courses/{course['id']}", {"name": "Old Seminar", "archived": True})
    assert r.status_code == 200 and r.json()["archived"] is True

    assert api.get("/api/courses").json() == []
    assert len(api.get("/api/courses?include_archived=true").json()) == 1
    assert api.get("/api/assignments").json() == []
    assert [x["id"] for x in api.get("/api/assignments?include_archived=true").json()] == [a["id"]]
    assert api.get(f"/api/assignments/{a['id']}").status_code == 200

    # New work can't be added to an archived course until it's restored.
    body = {"title": "Late add", "course_id": course["id"], "due_date": "2026-11-01"}
    assert api.post("/api/assignments", body).status_code == 422
    api.patch(f"/api/courses/{course['id']}", {"name": "Old Seminar", "archived": False})
    assert api.post("/api/assignments", body).status_code == 201


def test_delete_requires_matching_confirmed_count(api: Api) -> None:
    course = api.course("Chemistry")
    api.assignment(course["id"], "Lab 1")
    api.assignment(course["id"], "Lab 2")
    keep = api.course("Keep me")
    kept = api.assignment(keep["id"], "Unrelated")

    impact = api.get(f"/api/courses/{course['id']}/deletion-impact").json()
    assert impact == {"assignments": 2, "events": 0}

    # Confirmation is required, and a stale count (data changed since the user
    # confirmed) is refused instead of deleting more than they agreed to.
    assert api.delete(f"/api/courses/{course['id']}").status_code == 422
    stale = api.delete(f"/api/courses/{course['id']}?expected_assignments=1")
    assert stale.status_code == 409 and "2 assignments" in stale.json()["detail"]
    assert len(api.get("/api/assignments").json()) == 3

    assert api.delete(f"/api/courses/{course['id']}?expected_assignments=2").status_code == 204
    remaining = api.get("/api/assignments").json()
    assert [a["id"] for a in remaining] == [kept["id"]]
    assert [c["name"] for c in api.get("/api/courses").json()] == ["Keep me"]


def test_open_assignment_counts(api: Api) -> None:
    course = api.course("Biology")
    a = api.assignment(course["id"], "Reading")
    api.assignment(course["id"], "Quiz")
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": True})
    assert api.get("/api/courses").json()[0]["open_assignments"] == 1
