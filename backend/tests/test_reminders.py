"""Durable reminders: scheduling rules, the worker, retries, concurrency, and crash recovery."""

import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from conftest import Api, Clock
from sqlalchemy import delete, select

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.email import (
    CaptureSender,
    EmailMessage,
    PermanentEmailError,
    ResendSender,
    SendResult,
    TransientEmailError,
)
from app.models import Notification, Reminder, User
from app.reminders import sync_assignment
from app.worker import claim, main, process, resync_all, run_once

NY_OFFSET = timedelta(hours=4)  # EDT until Nov 1, 2026


def ny(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC) + NY_OFFSET


class FakeSender:
    """Records messages; replays scripted failures, then accepts."""

    name = "fake"

    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.sent: list[EmailMessage] = []
        self.failures = list(failures or [])
        self.events: dict[str, str] = {}

    def send(self, message: EmailMessage) -> SendResult:
        self.sent.append(message)
        if self.failures:
            raise self.failures.pop(0)
        return SendResult("accepted", f"msg-{len(self.sent)}")

    def last_event(self, provider_message_id: str) -> str | None:
        return self.events.get(provider_message_id)


def cfg(**overrides: Any) -> Settings:
    return get_settings().model_copy(update=overrides)


def run(clock: Clock, sender: Any = None, worker: str = "w1", **settings: Any) -> dict[str, int]:
    return run_once(sender or FakeSender(), cfg(**settings), worker, lambda: clock.now)


def jobs(**where: Any) -> list[Reminder]:
    with SessionLocal() as db:
        stmt = select(Reminder).order_by(Reminder.id)
        for k, v in where.items():
            stmt = stmt.where(getattr(Reminder, k) == v)
        return list(db.scalars(stmt))


def notifications() -> list[Notification]:
    with SessionLocal() as db:
        return list(db.scalars(select(Notification).order_by(Notification.id)))


def homework(api: Api, **fields: Any) -> dict[str, Any]:
    c = api.course()
    return api.assignment(c["id"], **fields)


# --- Scheduling rules --------------------------------------------------------------


def test_day_reminder_for_date_only_deadline_fires_at_9am(api: Api) -> None:
    homework(api, due_date="2026-10-20", reminder_minutes=1440)
    [r] = jobs()
    assert (r.channel, r.status) == ("in_app", "pending")  # email is opt-in
    assert r.send_at == ny(2026, 10, 19, 9)  # 9:00 AM the day before, New York time
    assert r.deadline_at == ny(2026, 10, 21, 0)  # overdue at local midnight after the date


def test_hour_reminder_for_date_only_counts_back_from_midnight(api: Api) -> None:
    homework(api, due_date="2026-10-20", reminder_minutes=60)
    assert jobs()[0].send_at == ny(2026, 10, 20, 23)


def test_timed_deadline_reminder(api: Api) -> None:
    homework(api, due_date="2026-10-20", due_time="17:00", reminder_minutes=180)
    assert jobs()[0].send_at == ny(2026, 10, 20, 14)


def test_reminder_time_already_passed_fires_now_if_deadline_has_not(api: Api, clock: Clock) -> None:
    homework(api, due_date="2026-10-15", reminder_minutes=10080)  # "1 week before", due tomorrow
    assert jobs()[0].send_at == clock.now


def test_no_reminder_when_turned_off_or_already_overdue(api: Api) -> None:
    c = api.course()
    api.assignment(c["id"], title="No reminder", reminder_minutes=None)
    api.assignment(c["id"], title="Already overdue", due_date="2026-10-01")
    assert jobs() == []


def test_email_opt_in_adds_and_removes_email_reminders(api: Api) -> None:
    homework(api)
    assert api.patch("/api/account", {"email_reminders": True}).json()["email_reminders"] is True
    assert {(r.channel, r.status) for r in jobs()} == {("in_app", "pending"), ("email", "pending")}
    api.patch("/api/account", {"email_reminders": False})
    assert {(r.channel, r.status) for r in jobs()} == {("in_app", "pending"), ("email", "cancelled")}


# --- Cancellation and rescheduling ---------------------------------------------------


def test_editing_the_deadline_reschedules(api: Api) -> None:
    a = homework(api, due_date="2026-10-20")
    body = {
        "title": a["title"],
        "course_id": a["course"]["id"],
        "due_date": "2026-10-22",
        "reminder_minutes": 1440,
    }
    assert api.put(f"/api/assignments/{a['id']}", body).status_code == 200
    old, new = jobs()
    assert (old.status, new.status) == ("cancelled", "pending")
    assert new.send_at == ny(2026, 10, 21, 9)

    # Saving again unchanged keeps the same pending reminder.
    api.put(f"/api/assignments/{a['id']}", body)
    assert len(jobs()) == 2

    # Turning the reminder off cancels it.
    api.put(f"/api/assignments/{a['id']}", body | {"reminder_minutes": None})
    assert jobs(status="pending") == []


def test_completing_cancels_and_reopening_restores(api: Api) -> None:
    a = homework(api)
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": True})
    assert [r.status for r in jobs()] == ["cancelled"]
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": False})
    assert [r.status for r in jobs()] == ["cancelled", "pending"]


def test_archiving_the_course_cancels_and_restoring_reschedules(api: Api) -> None:
    a = homework(api)
    course = {"name": a["course"]["name"], "color": a["course"]["color"]}
    api.patch(f"/api/courses/{a['course']['id']}", course | {"archived": True})
    assert [r.status for r in jobs()] == ["cancelled"]
    api.patch(f"/api/courses/{a['course']['id']}", course | {"archived": False})
    assert [r.status for r in jobs()] == ["cancelled", "pending"]


def test_time_zone_change_moves_date_only_reminders_but_not_timed_ones(api: Api) -> None:
    c = api.course()
    api.assignment(c["id"], title="Date only", due_date="2026-10-20", reminder_minutes=1440)
    api.assignment(c["id"], title="Timed", due_date="2026-10-20", due_time="17:00", reminder_minutes=180)
    api.patch("/api/account", {"time_zone": "America/Los_Angeles"})
    pending = {r.assignment_id: r.send_at for r in jobs(status="pending")}
    assert sorted(pending.values()) == [
        datetime(2026, 10, 19, 16, tzinfo=UTC),  # 9:00 AM Pacific
        ny(2026, 10, 20, 14),  # same instant as before
    ]


def test_deleting_an_assignment_removes_reminders_but_keeps_notifications(api: Api, clock: Clock) -> None:
    a = homework(api, due_date="2026-10-15")
    clock.advance(minutes=1)  # its reminder time has passed; the deadline hasn't
    run(clock)
    assert len(notifications()) == 1
    api.delete(f"/api/assignments/{a['id']}")
    assert jobs() == []
    [n] = notifications()
    assert (n.title, n.assignment_id) == ("Homework 1", None)


def test_concurrent_resyncs_leave_one_pending_reminder(api: Api, clock: Clock) -> None:
    a = homework(api)
    with SessionLocal() as db:
        user = db.get(User, api.user["id"])  # type: ignore[index]
        assert user is not None
        db.expunge(user)
    start = threading.Barrier(4)
    errors: list[BaseException] = []

    def resync() -> None:
        try:
            with SessionLocal() as db:
                start.wait()
                sync_assignment(db, a["id"], db.merge(user), clock.now)
                db.commit()
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=resync) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(jobs(status="pending")) == 1


# --- Worker delivery -------------------------------------------------------------------


def test_worker_delivers_in_app_when_due_and_not_before(api: Api, clock: Clock) -> None:
    homework(api, due_date="2026-10-20")
    assert run(clock) == {}
    clock.set(ny(2026, 10, 19, 9))
    assert run(clock) == {"delivered": 1}
    [r] = jobs()
    assert (r.status, r.attempts, r.finished_at) == ("delivered", 1, clock.now)

    listing = api.get("/api/notifications").json()
    assert listing["unread"] == 1
    item = listing["items"][0]
    assert (item["title"], item["body"]) == (
        "Homework 1",
        "Due Tue, Oct 20 (end of day) · Database Systems I",
    )
    api.post(f"/api/notifications/{item['id']}/read")
    assert api.get("/api/notifications").json()["unread"] == 0
    assert run(clock) == {}  # nothing left to do


def test_email_reminder_is_accepted_with_a_stable_idempotency_key(api: Api, clock: Clock) -> None:
    api.patch("/api/account", {"email_reminders": True})
    homework(api, due_date="2026-10-20", due_time="17:00", reminder_minutes=60)
    clock.set(ny(2026, 10, 20, 16))
    sender = FakeSender()
    assert run(clock, sender) == {"delivered": 1, "accepted": 1}
    [msg] = sender.sent
    email_job = jobs(channel="email")[0]
    assert msg.to == "alex@example.edu"
    assert msg.subject == "Reminder: Homework 1 is due Tue, Oct 20 at 5:00 PM"
    assert msg.idempotency_key == f"campusflow-reminder-{email_job.id}"
    assert (email_job.status, email_job.provider_message_id) == ("accepted", "msg-1")


def test_recheck_cancels_a_reminder_completed_after_it_was_claimed(api: Api, clock: Clock) -> None:
    a = homework(api, due_date="2026-10-15")
    clock.advance(minutes=1)  # its reminder time has passed; the deadline hasn't
    with SessionLocal() as db:
        [job_id] = claim(db, "w1", clock.now, timedelta(minutes=5))
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": True})
    with SessionLocal() as db:
        assert process(db, job_id, FakeSender(), cfg(), "w1", clock.now) == "cancelled"
    [r] = jobs()
    assert r.detail == "The assignment was completed."
    assert notifications() == []


def test_recheck_cancels_a_reminder_rescheduled_while_claimed(api: Api, clock: Clock) -> None:
    a = homework(api, due_date="2026-10-15")
    clock.advance(minutes=1)  # its reminder time has passed; the deadline hasn't
    with SessionLocal() as db:
        [job_id] = claim(db, "w1", clock.now, timedelta(minutes=5))
    body = {
        "title": a["title"],
        "course_id": a["course"]["id"],
        "due_date": "2026-10-25",
        "reminder_minutes": 1440,
    }
    api.put(f"/api/assignments/{a['id']}", body)
    with SessionLocal() as db:
        assert process(db, job_id, FakeSender(), cfg(), "w1", clock.now) == "cancelled"
    assert {r.status for r in jobs()} == {"cancelled", "pending"}  # the new one waits for its own time


def test_outage_policy_late_but_before_deadline_is_sent_after_deadline_is_skipped(
    api: Api, clock: Clock
) -> None:
    c = api.course()
    api.assignment(c["id"], title="Still ahead", due_date="2026-10-20")
    api.assignment(c["id"], title="Already due", due_date="2026-10-15")
    # The worker was down from before both reminders until noon on Oct 19, after
    # "Still ahead"'s 9:00 AM reminder time but before its deadline.
    clock.set(ny(2026, 10, 19, 12))
    assert run(clock) == {"delivered": 1, "skipped": 1}
    skipped = jobs(status="skipped")[0]
    assert skipped.detail == "The deadline passed before the reminder could be delivered."
    assert [n.title for n in notifications()] == ["Still ahead"]


def test_transient_errors_retry_with_backoff_then_succeed(api: Api, clock: Clock) -> None:
    api.patch("/api/account", {"email_reminders": True})
    homework(api, due_date="2026-10-20")
    clock.set(ny(2026, 10, 19, 9))
    sender = FakeSender([TransientEmailError("Resend 503"), TransientEmailError("network error")])

    run(clock, sender)
    email = jobs(channel="email")[0]
    assert (email.status, email.attempts) == ("pending", 1)
    assert timedelta(minutes=1) <= email.next_attempt_at - clock.now <= timedelta(minutes=1.2)
    assert email.detail == "Retrying: Resend 503"

    assert run(clock, sender) == {}  # not yet
    clock.advance(minutes=2)
    run(clock, sender)
    email = jobs(channel="email")[0]
    assert email.attempts == 2
    assert timedelta(minutes=4) <= email.next_attempt_at - clock.now <= timedelta(minutes=4.8)

    clock.advance(minutes=5)
    assert run(clock, sender) == {"accepted": 1}
    assert len({m.idempotency_key for m in sender.sent}) == 1  # every retry reused the key
    assert len(sender.sent) == 3


def test_gives_up_after_the_attempt_limit(api: Api, clock: Clock) -> None:
    api.patch("/api/account", {"email_reminders": True})
    homework(api, due_date="2026-10-20")
    clock.set(ny(2026, 10, 19, 9))
    sender = FakeSender([TransientEmailError("Resend 500") for _ in range(10)])
    for _ in range(6):
        run(clock, sender)
        clock.advance(hours=2)
    email = jobs(channel="email")[0]
    assert (email.status, email.attempts) == ("failed", 5)
    assert email.detail == "Gave up after 5 attempts: Resend 500"
    assert len(sender.sent) == 5


def test_permanent_errors_fail_without_retrying(api: Api, clock: Clock) -> None:
    api.patch("/api/account", {"email_reminders": True})
    homework(api, due_date="2026-10-20")
    clock.set(ny(2026, 10, 19, 9))
    error = PermanentEmailError(
        "Resend 403 validation_error: You can only send testing emails to your own address"
    )
    run(clock, FakeSender([error]))
    email = jobs(channel="email")[0]
    assert (email.status, email.attempts) == ("failed", 1)
    assert email.detail is not None and "testing emails" in email.detail
    assert jobs(channel="in_app")[0].status == "delivered"  # one channel failing doesn't block the other


def test_allowlist_keeps_email_to_listed_addresses_only(api: Api, clock: Clock) -> None:
    api.patch("/api/account", {"email_reminders": True})
    homework(api, due_date="2026-10-20")
    clock.set(ny(2026, 10, 19, 9))
    sender = FakeSender()
    run(clock, sender, email_allowed_recipients=["owner@example.edu"])
    assert sender.sent == []
    assert jobs(channel="email")[0].status == "skipped"
    assert jobs(channel="in_app")[0].status == "delivered"


def test_provider_confirmation_moves_accepted_to_delivered_or_failed(api: Api, clock: Clock) -> None:
    api.patch("/api/account", {"email_reminders": True})
    c = api.course()
    api.assignment(c["id"], title="One", due_date="2026-10-20")
    api.assignment(c["id"], title="Two", due_date="2026-10-20")
    clock.set(ny(2026, 10, 19, 9))
    sender = FakeSender()
    run(clock, sender)
    assert {r.status for r in jobs(channel="email")} == {"accepted"}  # no evidence of delivery yet

    sender.events = {"msg-1": "delivered", "msg-2": "bounced"}
    clock.advance(minutes=11)
    run(clock, sender)
    one, two = jobs(channel="email")
    assert (one.status, one.provider_status) == ("delivered", "delivered")
    assert (two.status, two.detail) == ("failed", "The provider reported: bounced.")


def test_capture_mode_writes_a_file_and_never_claims_sending(api: Api, clock: Clock, tmp_path: Path) -> None:
    api.patch("/api/account", {"email_reminders": True})
    homework(api, due_date="2026-10-20")
    clock.set(ny(2026, 10, 19, 9))
    run(clock, CaptureSender(str(tmp_path)))
    email = jobs(channel="email")[0]
    assert (email.status, email.provider_message_id) == ("captured", None)
    assert email.detail == "Saved to the local capture file; not sent."
    [line] = (tmp_path / "captured-emails.jsonl").read_text().splitlines()
    assert json.loads(line)["subject"] == "Reminder: Homework 1 is due Tue, Oct 20 (end of day)"


# --- Concurrency and crash recovery -----------------------------------------------------


def test_concurrent_workers_never_claim_the_same_job(api: Api, clock: Clock) -> None:
    c = api.course()
    for i in range(30):
        api.assignment(c["id"], title=f"Task {i}", due_date="2026-10-20")
    clock.set(ny(2026, 10, 19, 9))
    start = threading.Barrier(3)
    claimed: dict[str, list[int]] = {}

    def worker(name: str) -> None:
        mine: list[int] = []
        with SessionLocal() as db:
            start.wait()
            while ids := claim(db, name, clock.now, timedelta(minutes=5), limit=4):
                mine += ids
        claimed[name] = mine

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    all_ids = [i for ids in claimed.values() for i in ids]
    assert len(all_ids) == len(set(all_ids)) == 30  # every job exactly once

    with SessionLocal() as db:
        for name, ids in claimed.items():
            for job_id in ids:
                assert process(db, job_id, FakeSender(), cfg(), name, clock.now) == "delivered"
    assert len(notifications()) == 30


def test_crashed_worker_job_is_reclaimed_after_its_lease(api: Api, clock: Clock) -> None:
    homework(api, due_date="2026-10-15")
    clock.advance(minutes=1)  # its reminder time has passed; the deadline hasn't
    lease = timedelta(minutes=5)
    with SessionLocal() as db:
        [job_id] = claim(db, "crashed", clock.now, lease)  # ...and then the process dies

    clock.advance(minutes=1)
    with SessionLocal() as db:
        assert claim(db, "w2", clock.now, lease) == []  # lease still held
    clock.advance(minutes=5)
    with SessionLocal() as db:
        assert claim(db, "w2", clock.now, lease) == [job_id]
        assert process(db, job_id, FakeSender(), cfg(), "w2", clock.now) == "delivered"
        # The original worker waking up late can't deliver it again.
        assert process(db, job_id, FakeSender(), cfg(), "crashed", clock.now) == "lost"
    [r] = jobs()
    assert (r.status, r.attempts, r.worker_id) == ("delivered", 2, "w2")
    assert len(notifications()) == 1


def test_a_job_that_keeps_crashing_its_worker_eventually_fails(api: Api, clock: Clock) -> None:
    homework(api, due_date="2026-10-20")
    clock.set(ny(2026, 10, 19, 9))
    lease = timedelta(minutes=5)
    with SessionLocal() as db:
        for _ in range(6):  # claimed, then the worker dies, six times
            [job_id] = claim(db, "w", clock.now, lease)
            clock.advance(minutes=6)
        assert process(db, job_id, FakeSender(), cfg(), "w", clock.now) == "failed"
    assert jobs()[0].detail == "Gave up after 5 attempts."


def test_resync_rebuilds_missing_reminders(api: Api, clock: Clock) -> None:
    homework(api)
    with SessionLocal() as db:
        db.execute(delete(Reminder))
        db.commit()
    resync_all(clock.now)
    assert [r.status for r in jobs()] == ["pending"]


# --- Privacy and logging -------------------------------------------------------------------


def test_other_users_cannot_see_reminders_or_notifications(api: Api, other: Api, clock: Clock) -> None:
    a = homework(api, due_date="2026-10-15")
    clock.advance(minutes=1)  # its reminder time has passed; the deadline hasn't
    run(clock)
    [n] = api.get("/api/notifications").json()["items"]
    assert api.get(f"/api/assignments/{a['id']}/reminders").status_code == 200
    assert other.get(f"/api/assignments/{a['id']}/reminders").status_code == 404
    assert other.post(f"/api/notifications/{n['id']}/read").status_code == 404
    assert other.get("/api/notifications").json() == {"unread": 0, "items": []}
    other.post("/api/notifications/read-all")
    assert api.get("/api/notifications").json()["unread"] == 1


def test_reminder_history_endpoint(api: Api, clock: Clock) -> None:
    a = homework(api, due_date="2026-10-20")
    history = api.get(f"/api/assignments/{a['id']}/reminders").json()
    assert [(h["channel"], h["status"], h["send_at"]) for h in history] == [
        ("in_app", "pending", "2026-10-19T13:00:00Z")
    ]


# --- Resend adapter (HTTP is simulated: this environment can't reach api.resend.com) ----------


class FakeResponse(BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        pass


def http_error(code: int, name: str, message: str = "x") -> urllib.error.HTTPError:
    body = BytesIO(json.dumps({"statusCode": code, "name": name, "message": message}).encode())
    return urllib.error.HTTPError("https://api.resend.com/emails", code, name, {}, body)  # type: ignore[arg-type]


def test_resend_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[urllib.request.Request] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float) -> FakeResponse:
        seen.append(req)
        return FakeResponse(b'{"id": "4ef9a417"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    sender = ResendSender("re_secret_key", "CampusFlow <onboarding@resend.dev>")
    result = sender.send(EmailMessage("a@example.edu", "Subject", "Body", "campusflow-reminder-7"))
    assert result == SendResult("accepted", "4ef9a417")
    [req] = seen
    assert (req.full_url, req.get_method()) == ("https://api.resend.com/emails", "POST")
    assert req.get_header("Idempotency-key") == "campusflow-reminder-7"
    assert req.get_header("Authorization") == "Bearer re_secret_key"
    assert json.loads(req.data or b"") == {  # type: ignore[arg-type]
        "from": "CampusFlow <onboarding@resend.dev>",
        "to": ["a@example.edu"],
        "subject": "Subject",
        "text": "Body",
    }


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (
            http_error(403, "validation_error", "You can only send testing emails to your own email address"),
            PermanentEmailError,
        ),
        (http_error(401, "validation_error", "API key is invalid"), PermanentEmailError),
        (http_error(422, "missing_required_field"), PermanentEmailError),
        (http_error(409, "invalid_idempotent_request"), PermanentEmailError),
        (http_error(409, "concurrent_idempotent_requests"), TransientEmailError),
        (http_error(429, "rate_limit_exceeded"), TransientEmailError),
        (http_error(500, "application_error"), TransientEmailError),
        (urllib.error.URLError("connection refused"), TransientEmailError),
    ],
)
def test_resend_errors_are_classified(monkeypatch: pytest.MonkeyPatch, error: Exception, kind: type) -> None:
    def fake_urlopen(req: urllib.request.Request, timeout: float) -> FakeResponse:
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(kind) as caught:
        ResendSender("re_secret_key", "x@example.dev").send(EmailMessage("a@b.edu", "s", "t", "k"))
    assert "re_secret_key" not in str(caught.value)


def test_logs_mask_addresses_and_never_include_the_api_key(
    api: Api, clock: Clock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: FakeResponse(b'{"id": "m1"}'))
    api.patch("/api/account", {"email_reminders": True})
    homework(api, due_date="2026-10-20")
    clock.set(ny(2026, 10, 19, 9))
    with caplog.at_level(logging.INFO):
        run(clock, ResendSender("re_secret_key", "x@example.dev"))
    text = caplog.text
    assert "a***@example.edu" in text
    assert "alex@example.edu" not in text and "re_secret_key" not in text


def test_worker_command_line_once_and_resync(
    api: Api, clock: Clock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`python -m app.worker --once` with the default capture mode, as a scheduler would run it."""
    monkeypatch.setenv("CAMPUSFLOW_EMAIL_CAPTURE_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        api.patch("/api/account", {"email_reminders": True})
        # The command uses the real clock: due in 3 days with a 1-week lead is due now, whenever "now" is.
        due = (datetime.now(UTC) + timedelta(days=3)).date().isoformat()
        homework(api, due_date=due, reminder_minutes=10080)
        main(["--resync"])  # idempotent: the reminders already exist
        assert len(jobs(status="pending")) == 2
        main(["--once"])
    finally:
        get_settings.cache_clear()
    assert {(r.channel, r.status) for r in jobs()} == {("in_app", "delivered"), ("email", "captured")}
    assert len((tmp_path / "captured-emails.jsonl").read_text().splitlines()) == 1


# --- One delivery per logical reminder, across its whole lifecycle -------------------------
#
# A "logical reminder" is one channel, one lead time, and one deadline (for a date-only
# deadline, the due *date*, so a time zone change doesn't make it a new reminder).
# Once it has been delivered, captured, accepted, failed, or skipped, it is never
# scheduled again. Only a cancelled one (never attempted) can be scheduled again.


def deliver_now(api: Api, clock: Clock, sender: FakeSender, email: bool = True) -> dict[str, Any]:
    """An assignment whose 1-day reminder time has passed (due tomorrow), delivered once."""
    if email:
        api.patch("/api/account", {"email_reminders": True})
    a = homework(api, due_date="2026-10-15")
    clock.advance(minutes=1)
    expected = {"delivered": 1, "accepted": 1} if email else {"delivered": 1}
    assert run(clock, sender) == expected
    return a


def edit(api: Api, a: dict[str, Any], **changes: Any) -> dict[str, Any]:
    body = {
        "title": a["title"],
        "course_id": a["course"]["id"],
        "due_date": a["due_date"],
        "description": a["description"],
        "reminder_minutes": a["reminder_minutes"],
    } | changes
    r = api.put(f"/api/assignments/{a['id']}", body)
    assert r.status_code == 200, r.text
    return dict(r.json())


def deliveries(sender: FakeSender) -> tuple[int, int]:
    return len(notifications()), len(sender.sent)


def test_editing_title_or_description_after_delivery_sends_nothing_new(api: Api, clock: Clock) -> None:
    sender = FakeSender()
    a = deliver_now(api, clock, sender)
    a = edit(api, a, title="Homework 1 (revised)")
    edit(api, a, description="Bring a calculator")
    assert jobs(status="pending") == []
    clock.advance(minutes=5)
    assert run(clock, sender) == {}
    assert deliveries(sender) == (1, 1)
    assert len(jobs()) == 2  # the original in-app and email reminders, nothing added


def test_saving_unchanged_settings_after_delivery_sends_nothing_new(api: Api, clock: Clock) -> None:
    sender = FakeSender()
    deliver_now(api, clock, sender)
    for _ in range(2):
        r = api.patch("/api/account", {"time_zone": "America/New_York", "email_reminders": True})
        assert r.status_code == 200
    clock.advance(minutes=5)
    assert run(clock, sender) == {}
    assert deliveries(sender) == (1, 1)


def test_repeated_resync_before_and_after_delivery_never_duplicates(api: Api, clock: Clock) -> None:
    api.patch("/api/account", {"email_reminders": True})
    homework(api, due_date="2026-10-20")
    for _ in range(3):
        resync_all(clock.now)
    assert len(jobs()) == 2  # one in-app, one email, both still pending
    clock.set(ny(2026, 10, 19, 9))
    sender = FakeSender()
    assert run(clock, sender) == {"delivered": 1, "accepted": 1}
    for _ in range(3):
        resync_all(clock.now)
        assert run(clock, sender) == {}
    assert len(jobs()) == 2
    assert deliveries(sender) == (1, 1)


def test_syncing_while_a_matching_job_is_processing_adds_no_job_and_no_delivery(
    api: Api, clock: Clock
) -> None:
    api.patch("/api/account", {"email_reminders": True})
    a = homework(api, due_date="2026-10-15")
    clock.advance(minutes=1)
    with SessionLocal() as db:
        claimed = claim(db, "w1", clock.now, timedelta(minutes=5))
    assert len(claimed) == 2
    # Every kind of resync arrives while the worker holds both jobs.
    edit(api, a, title="Renamed while sending")
    api.patch("/api/account", {"time_zone": "America/New_York", "email_reminders": True})
    resync_all(clock.now)
    assert len(jobs()) == 2  # nothing queued behind the in-flight jobs
    sender = FakeSender()
    with SessionLocal() as db:
        outcomes = {process(db, job_id, sender, cfg(), "w1", clock.now) for job_id in claimed}
    assert outcomes == {"delivered", "accepted"}
    clock.advance(minutes=10)
    assert run(clock, sender) == {}
    assert deliveries(sender) == (1, 1)


def test_changing_the_deadline_after_delivery_schedules_one_replacement(api: Api, clock: Clock) -> None:
    sender = FakeSender()
    a = deliver_now(api, clock, sender, email=False)
    edit(api, a, due_date="2026-10-20")
    [replacement] = jobs(status="pending")
    assert (replacement.channel, replacement.send_at) == ("in_app", ny(2026, 10, 19, 9))
    resync_all(clock.now)
    assert len(jobs(status="pending")) == 1
    clock.set(ny(2026, 10, 19, 9))
    assert run(clock, sender) == {"delivered": 1}  # the new deadline's reminder, once
    assert [n.body.split(" · ")[0] for n in notifications()] == [
        "Due Thu, Oct 15 (end of day)",
        "Due Tue, Oct 20 (end of day)",
    ]


def test_changing_the_reminder_lead_time_schedules_the_replacement(api: Api, clock: Clock) -> None:
    a = homework(api, due_date="2026-10-20", reminder_minutes=1440)
    edit(api, a, reminder_minutes=60)
    old, new = jobs()
    assert (old.status, new.status) == ("cancelled", "pending")
    assert new.send_at == ny(2026, 10, 20, 23)
    edit(api, a, reminder_minutes=None)
    assert jobs(status="pending") == []


def test_completing_and_reopening_never_resends_a_delivered_reminder(api: Api, clock: Clock) -> None:
    sender = FakeSender()
    a = deliver_now(api, clock, sender)
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": True})
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": False})
    assert jobs(status="pending") == []
    clock.advance(minutes=5)
    assert run(clock, sender) == {}
    assert deliveries(sender) == (1, 1)


def test_reopening_before_the_reminder_fired_restores_it_once(api: Api, clock: Clock) -> None:
    a = homework(api, due_date="2026-10-20")
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": True})
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": False})
    assert len(jobs(status="pending")) == 1
    clock.set(ny(2026, 10, 19, 9))
    assert run(clock) == {"delivered": 1}
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": True})
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": False})
    assert run(clock) == {}
    assert len(notifications()) == 1


def test_completed_after_claim_then_reopened_is_delivered_once(api: Api, clock: Clock) -> None:
    a = homework(api, due_date="2026-10-15")
    clock.advance(minutes=1)
    with SessionLocal() as db:
        [job_id] = claim(db, "w1", clock.now, timedelta(minutes=5))
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": True})
    with SessionLocal() as db:
        assert process(db, job_id, FakeSender(), cfg(), "w1", clock.now) == "cancelled"
    api.post(f"/api/assignments/{a['id']}/completion", {"completed": False})
    assert run(clock) == {"delivered": 1}  # it was never delivered, so reopening brings it back
    assert run(clock) == {}
    assert len(notifications()) == 1


def test_time_zone_change_after_delivery_does_not_resend_a_date_only_reminder(api: Api, clock: Clock) -> None:
    sender = FakeSender()
    deliver_now(api, clock, sender)
    api.patch("/api/account", {"time_zone": "America/Los_Angeles"})
    assert jobs(status="pending") == []
    clock.advance(hours=4)
    assert run(clock, sender) == {}
    assert deliveries(sender) == (1, 1)


def test_time_zone_change_before_delivery_moves_the_pending_reminder(api: Api, clock: Clock) -> None:
    homework(api, due_date="2026-10-20")
    api.patch("/api/account", {"time_zone": "America/Los_Angeles"})
    api.patch("/api/account", {"time_zone": "America/Los_Angeles"})
    [pending] = jobs(status="pending")
    assert pending.send_at == datetime(2026, 10, 19, 16, tzinfo=UTC)  # 9:00 AM Pacific


def test_email_opt_in_after_the_reminder_fired_sends_no_late_email(api: Api, clock: Clock) -> None:
    sender = FakeSender()
    deliver_now(api, clock, sender, email=False)
    api.patch("/api/account", {"email_reminders": True})
    assert jobs(channel="email") == []
    assert run(clock, sender) == {}
    assert sender.sent == []


def test_email_opt_out_and_back_in_before_it_fires_sends_one_email(api: Api, clock: Clock) -> None:
    homework(api, due_date="2026-10-20")
    for on in (True, False, True, True):
        api.patch("/api/account", {"email_reminders": on})
    assert len(jobs(channel="email", status="pending")) == 1
    clock.set(ny(2026, 10, 19, 9))
    sender = FakeSender()
    assert run(clock, sender) == {"delivered": 1, "accepted": 1}
    assert run(clock, sender) == {}
    assert len(sender.sent) == 1
