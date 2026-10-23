"""Production plumbing: database URLs from hosts, client IPs behind proxies, security
headers, serving the built frontend, and creating the demo on first start."""

from pathlib import Path

import pytest
from conftest import Api, Clock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import demo
from app.config import Settings, get_settings
from app.db import SessionLocal
from app.main import serve_frontend
from app.models import User


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+psycopg"])
def test_host_database_urls_get_the_psycopg_driver(scheme: str) -> None:
    url = f"{scheme}://u:p@db.example.com/campusflow?sslmode=require"
    assert Settings(database_url=url).database_url == (
        "postgresql+psycopg://u:p@db.example.com/campusflow?sslmode=require"
    )


@pytest.mark.parametrize("raw", ['["me@example.com"]', "me@example.com", " me@example.com, "])
def test_recipient_allowlist_accepts_json_or_plain(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAMPUSFLOW_EMAIL_ALLOWED_RECIPIENTS", raw)
    assert Settings().email_allowed_recipients == ["me@example.com"]


def test_rate_limits_ignore_forwarded_entries_the_client_wrote(
    anon: Api, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "trusted_proxy_hops", 1)
    with SessionLocal() as db:
        demo.reset_demo(db, clock.now)
        db.commit()

    def login(forwarded: str) -> int:
        r = anon.client.post("/api/auth/demo", headers={"X-Forwarded-For": forwarded})
        return int(r.status_code)

    # A visitor varies the part of the header they control; our proxy's entry stays the same.
    assert all(login(f"10.0.0.{i}, 203.0.113.7") == 200 for i in range(30))
    assert login("10.0.0.99, 203.0.113.7") == 429
    assert login("203.0.113.8") == 200  # a different visitor has their own allowance


def test_every_response_has_security_headers(anon: Api) -> None:
    for r in (anon.get("/api/health"), anon.get("/api/auth/session"), anon.get("/api/nope")):
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert r.headers["x-content-type-options"] == "nosniff"


def test_serves_the_built_frontend(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    (dist / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("not public")
    site = FastAPI()
    serve_frontend(site, dist)
    client = TestClient(site)

    asset = client.get("/assets/index-abc123.js")
    assert asset.text == "console.log(1)"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/favicon.svg").headers["cache-control"] == "no-cache"
    for path in ("/", "/week", "/assignments/12", "/assets/%2e%2e/%2e%2e/secret.txt"):
        r = client.get(path)
        assert (r.status_code, r.text, r.headers["cache-control"]) == (200, "<div id=root></div>", "no-cache")
    assert client.get("/api/unknown").status_code == 404

    # HEAD (uptime checks, `curl -I`) answers like GET, minus the body.
    for path, cache in [
        ("/", "no-cache"),
        ("/week", "no-cache"),
        ("/assets/index-abc123.js", "public, max-age=31536000, immutable"),
        ("/assets/%2e%2e/%2e%2e/secret.txt", "no-cache"),  # still the app shell, never the file
    ]:
        get, head = client.get(path), client.head(path)
        assert (head.status_code, head.content, head.headers["cache-control"]) == (200, b"", cache)
        assert head.headers["content-type"] == get.headers["content-type"]
        assert head.headers["content-length"] == str(len(get.content))
    assert client.head("/api/unknown").status_code == 404


def test_server_start_creates_the_demo_only_when_missing(clock: Clock) -> None:
    def demo_id() -> int | None:
        with SessionLocal() as db:
            return db.scalar(select(User.id).where(User.is_demo))

    assert demo_id() is None
    demo.main(["--if-missing"])
    first = demo_id()
    assert first is not None
    demo.main(["--if-missing"])
    assert demo_id() == first  # visitors' changes survive restarts until the nightly reset
    demo.main([])
    assert demo_id() not in (None, first)


def test_settings_say_when_this_server_cannot_email_you(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Resend's free plan only delivers to the account owner, listed in the allowlist.
    monkeypatch.setattr(get_settings(), "email_mode", "resend")
    monkeypatch.setattr(get_settings(), "email_allowed_recipients", ["Owner@Example.edu"])
    owner, visitor = Api(), Api()
    assert owner.register("owner@example.edu").json()["user"]["email_delivery"] == "resend"
    assert visitor.register("visitor@example.edu").json()["user"]["email_delivery"] == "unavailable"
    monkeypatch.setattr(get_settings(), "email_allowed_recipients", [])
    assert visitor.get("/api/auth/session").json()["user"]["email_delivery"] == "resend"
