"""Registration, login, sessions, CSRF, origin checks, and login rate limiting."""

from conftest import Api, Clock

from app.config import get_settings


def test_register_sets_httponly_session_cookie(anon: Api) -> None:
    r = anon.register("sam@example.edu")
    cookie = r.headers["set-cookie"]
    assert "cf_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert r.json()["user"]["email"] == "sam@example.edu"
    assert anon.get("/api/auth/session").json()["user"]["email"] == "sam@example.edu"


def test_email_is_normalized_and_unique(anon: Api) -> None:
    anon.register("Sam@Example.EDU")
    again = Api().post("/api/auth/register", {"email": " sam@example.edu", "password": "another password 1"})
    # EmailStr rejects the leading space or we normalize it; either way no second account.
    assert again.status_code in (409, 422)
    dup = Api().post("/api/auth/register", {"email": "SAM@example.edu", "password": "another password 1"})
    assert dup.status_code == 409


def test_registration_validates_password_and_zone(anon: Api) -> None:
    short = anon.post("/api/auth/register", {"email": "a@b.co", "password": "short"})
    assert short.status_code == 422
    bad_zone = anon.post(
        "/api/auth/register", {"email": "a@b.co", "password": "long enough pw", "time_zone": "Mars/Base"}
    )
    assert bad_zone.status_code == 422


def test_login_and_wrong_password(anon: Api, api: Api) -> None:
    assert anon.login("alex@example.edu", "wrong password").status_code == 401
    assert anon.login("nobody@example.edu", "whatever pass").status_code == 401
    # Same message either way, so the response doesn't reveal which emails exist.
    assert (
        anon.login("alex@example.edu", "wrong password").json()
        == anon.login("nobody@example.edu", "whatever pass").json()
    )
    assert anon.login("ALEX@example.edu").status_code == 200


def test_logout_invalidates_session_server_side(api: Api) -> None:
    cookie = api.client.cookies.get("cf_session")
    assert cookie is not None
    assert api.post("/api/auth/logout").status_code == 204
    assert api.get("/api/courses").status_code == 401
    # Replaying the old cookie value doesn't work either: the session row is gone.
    replay = Api()
    replay.client.cookies.set("cf_session", cookie)
    assert replay.get("/api/courses").status_code == 401


def test_session_expires_after_idle_period(api: Api, clock: Clock) -> None:
    assert api.get("/api/courses").status_code == 200
    clock.advance(days=get_settings().session_idle_days, minutes=1)
    assert api.get("/api/courses").status_code == 401


def test_activity_extends_session_up_to_absolute_limit(api: Api, clock: Clock) -> None:
    s = get_settings()
    assert (s.session_idle_days, s.session_absolute_days) == (7, 30)
    for _ in range(4):  # active every 6 days: days 6, 12, 18, 24 all still signed in
        clock.advance(days=6)
        assert api.get("/api/courses").status_code == 200
    clock.advance(days=6)  # day 30: the absolute lifetime has ended despite activity
    assert api.get("/api/courses").status_code == 401


def test_csrf_token_required_for_writes(api: Api) -> None:
    token = api.csrf
    api.csrf = None
    assert api.post("/api/courses", {"name": "No token"}).status_code == 403
    api.csrf = "forged"
    assert api.post("/api/courses", {"name": "Wrong token"}).status_code == 403
    api.csrf = token
    assert api.post("/api/courses", {"name": "Right token"}).status_code == 201


def test_cross_origin_writes_are_rejected(anon: Api) -> None:
    r = anon.post(
        "/api/auth/login",
        {"email": "x@example.edu", "password": "whatever"},
        headers={"Origin": "https://evil.example"},
    )
    assert r.status_code == 403
    ok = anon.post(
        "/api/auth/register",
        {"email": "y@example.edu", "password": "long enough pw"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert ok.status_code == 201


def test_login_rate_limit_by_email_survives_new_clients(api: Api, clock: Clock) -> None:
    limit = get_settings().login_max_failures_per_email
    for _ in range(limit):
        assert Api().login("alex@example.edu", "bad guess!").status_code == 401
    # Even the correct password is refused now, from a brand-new client, because
    # the counter lives in PostgreSQL rather than in one process's memory.
    assert Api().login("alex@example.edu").status_code == 429
    clock.advance(minutes=get_settings().login_window_minutes, seconds=1)
    assert Api().login("alex@example.edu").status_code == 200


def test_successful_login_resets_email_failures(api: Api) -> None:
    limit = get_settings().login_max_failures_per_email
    for _ in range(limit - 1):
        Api().login("alex@example.edu", "bad guess!")
    assert Api().login("alex@example.edu").status_code == 200
    for _ in range(limit - 1):
        assert Api().login("alex@example.edu", "bad guess!").status_code == 401


def test_password_change_signs_out_other_sessions(api: Api) -> None:
    laptop = Api()
    laptop.login("alex@example.edu")
    r = api.post(
        "/api/account/password",
        {"current_password": "correct horse battery", "new_password": "a brand new passphrase"},
    )
    assert r.status_code == 204
    assert laptop.get("/api/courses").status_code == 401
    assert api.get("/api/courses").status_code == 200
    assert Api().login("alex@example.edu", "a brand new passphrase").status_code == 200


def test_password_change_requires_current_password(api: Api) -> None:
    r = api.post("/api/account/password", {"current_password": "nope nope", "new_password": "something long"})
    assert r.status_code == 400


def test_delete_account_removes_everything(api: Api) -> None:
    course = api.course()
    api.assignment(course["id"])
    assert api.delete("/api/account", {"password": "wrong"}).status_code == 400
    assert api.delete("/api/account", {"password": "correct horse battery"}).status_code == 204
    assert api.get("/api/courses").status_code == 401
    assert Api().login("alex@example.edu").status_code == 401
