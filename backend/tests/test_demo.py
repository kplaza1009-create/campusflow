"""The shared demo account: reset, one-click login, restrictions, and no special access."""

from typing import Any

from conftest import Api, Clock
from sqlalchemy import func, select

from app.db import SessionLocal
from app.demo import DEMO_EMAIL, reset_demo
from app.models import Assignment, Course, Event, Reminder, User, UserSession


def reset(clock: Clock) -> int:
    with SessionLocal() as db:
        user = reset_demo(db, clock.now)
        db.commit()
        return user.id


def counts(user_id: int) -> dict[str, int]:
    models: list[tuple[str, Any]] = [
        ("courses", Course),
        ("assignments", Assignment),
        ("events", Event),
        ("reminders", Reminder),
        ("sessions", UserSession),
    ]
    with SessionLocal() as db:
        return {
            name: db.scalar(select(func.count()).select_from(model).where(model.user_id == user_id)) or 0
            for name, model in models
        }


def demo_login(client: Api) -> dict[str, Any]:
    r = client.post("/api/auth/demo")
    assert r.status_code == 200, r.text
    client.csrf, client.user = r.json()["csrf_token"], r.json()["user"]
    return dict(r.json()["user"])


def test_reset_builds_realistic_data_relative_to_today(anon: Api, clock: Clock) -> None:
    uid = reset(clock)
    assert counts(uid) | {"sessions": 0} == {
        "courses": 5,
        "assignments": 15,
        "events": 7,
        "reminders": 9,  # in-app only: one per open assignment whose deadline is still ahead
        "sessions": 0,
    }
    user = demo_login(anon)
    assert (user["email"], user["is_demo"], user["email_reminders"]) == (DEMO_EMAIL, True, False)
    today = anon.get("/api/today").json()
    assert [len(today[k]) for k in ("overdue", "due_today")] == [2, 2]
    assert any(
        o["conflicts_with"] for o in anon.get("/api/calendar?start=2026-10-12&days=7").json()["occurrences"]
    )


def test_reset_wipes_visitor_changes_and_signs_everyone_out(anon: Api, clock: Clock) -> None:
    reset(clock)
    demo_login(anon)
    anon.course("Visitor's course")
    clock.advance(days=1)
    uid = reset(clock)
    assert counts(uid)["courses"] == 5
    assert counts(uid)["sessions"] == 0
    assert anon.get("/api/today").status_code == 401  # the old session went with the old account
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(User).where(User.is_demo)) == 1


def test_demo_login_without_a_demo_account_says_so(anon: Api) -> None:
    r = anon.post("/api/auth/demo")
    assert r.status_code == 503 and "being reset" in r.json()["detail"]


def test_demo_login_is_rate_limited_per_ip(anon: Api, clock: Clock) -> None:
    reset(clock)
    for _ in range(30):
        assert anon.post("/api/auth/demo").status_code == 200
    assert anon.post("/api/auth/demo").status_code == 429
    clock.advance(hours=1, minutes=1)
    assert anon.post("/api/auth/demo").status_code == 200


def test_demo_cannot_change_password_delete_itself_or_turn_on_email(anon: Api, clock: Clock) -> None:
    reset(clock)
    demo_login(anon)
    r = anon.post("/api/account/password", {"current_password": "x", "new_password": "a long new password"})
    assert r.status_code == 403 and "shared demo" in r.json()["detail"]
    assert anon.delete("/api/account", {"password": "x"}).status_code == 403
    assert anon.patch("/api/account", {"email_reminders": True}).status_code == 403
    # Ordinary changes work, and are what the nightly reset undoes.
    assert anon.patch("/api/account", {"time_zone": "America/Chicago"}).status_code == 200
    assert anon.patch("/api/account", {"email_reminders": False}).status_code == 200


def test_demo_has_no_special_access(anon: Api, other: Api, clock: Clock) -> None:
    c = other.course("Private")
    a = other.assignment(c["id"], "Private work")
    reset(clock)
    demo_login(anon)
    assert anon.get(f"/api/assignments/{a['id']}").status_code == 404
    assert anon.get(f"/api/courses/{c['id']}/deletion-impact").status_code == 404
    titles = {x["title"] for x in anon.get("/api/assignments?status=all").json()}
    assert "Private work" not in titles


def test_demo_never_schedules_email_even_if_the_flag_is_set(anon: Api, clock: Clock) -> None:
    uid = reset(clock)
    with SessionLocal() as db:
        db.get(User, uid).email_reminders = True  # type: ignore[union-attr]
        db.commit()
    demo_login(anon)
    anon.patch("/api/account", {"time_zone": "America/New_York"})  # triggers a resync
    with SessionLocal() as db:
        channels = set(db.scalars(select(Reminder.channel).where(Reminder.user_id == uid)))
    assert channels == {"in_app"}


def test_demo_address_cannot_be_registered(anon: Api) -> None:
    r = anon.post("/api/auth/register", {"email": DEMO_EMAIL, "password": "correct horse battery"})
    assert r.status_code == 422
