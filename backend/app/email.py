"""Email delivery behind a small adapter, so the provider can be swapped.

Two implementations:
- CaptureSender (default): writes each message to a local JSON Lines file and sends
  nothing. Its result says "captured", never "sent".
- ResendSender: calls Resend's HTTP API with Python's standard library (no SDK).
  A 200 means Resend *accepted* the message, not that it was delivered. Delivery is
  confirmed later by asking Resend for the message's last event.

Errors are split into transient (worth retrying: rate limits, 5xx, network) and
permanent (retrying won't help: bad key, test-mode recipient, invalid request).
"""

import json
import logging
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from app.config import Settings

log = logging.getLogger("campusflow.email")


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    # Sent as Resend's Idempotency-Key: a retry of the same reminder within 24 hours
    # returns the original result instead of sending a second email.
    idempotency_key: str


@dataclass(frozen=True)
class SendResult:
    outcome: Literal["captured", "accepted"]
    provider_message_id: str | None = None


class TransientEmailError(Exception):
    """Try again later."""


class PermanentEmailError(Exception):
    """Retrying the same message won't help."""


class EmailSender(Protocol):
    name: str

    def send(self, message: EmailMessage) -> SendResult: ...

    def last_event(self, provider_message_id: str) -> str | None:
        """The provider's latest status for a sent message (e.g. "delivered"), if it tracks one."""
        ...


def mask_email(address: str) -> str:
    """alex.rivera@example.edu -> a***@example.edu, for logs."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


class CaptureSender:
    name = "capture"

    def __init__(self, directory: str) -> None:
        self.path = Path(directory) / "captured-emails.jsonl"

    def send(self, message: EmailMessage) -> SendResult:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {"captured_at": datetime.now(UTC).isoformat(), **asdict(message)}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        log.info("captured email to=%s file=%s", mask_email(message.to), self.path)
        return SendResult("captured")

    def last_event(self, provider_message_id: str) -> str | None:
        return None


TRANSIENT_CODES = frozenset({"concurrent_idempotent_requests", "resource_locked", "rate_limit_exceeded"})


class ResendSender:
    name = "resend"

    def __init__(
        self, api_key: str, from_address: str, base_url: str = "https://api.resend.com", timeout: float = 15
    ) -> None:
        self._api_key = api_key  # never logged or included in errors
        self.from_address = from_address
        self.base_url = base_url
        self.timeout = timeout

    def _request(
        self, method: str, path: str, body: dict[str, object] | None, headers: dict[str, str]
    ) -> dict[str, object]:
        req = urllib.request.Request(
            self.base_url + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": "campusflow-worker",
                **headers,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return dict(json.loads(resp.read() or b"{}"))
        except urllib.error.HTTPError as exc:
            raise self._classify(exc) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise TransientEmailError(f"network error: {type(exc).__name__}") from None

    def _classify(self, exc: urllib.error.HTTPError) -> Exception:
        try:
            payload = json.loads(exc.read() or b"{}")
        except ValueError:
            payload = {}
        name = str(payload.get("name", "")) if isinstance(payload, dict) else ""
        message = str(payload.get("message", "")) if isinstance(payload, dict) else ""
        summary = f"Resend {exc.code} {name}: {message}".strip()[:280]
        if exc.code == 429 and name in {"daily_quota_exceeded", "monthly_quota_exceeded"}:
            return TransientEmailError(summary)  # resets later; backoff covers it
        if exc.code >= 500 or exc.code == 429 or name in TRANSIENT_CODES:
            return TransientEmailError(summary)
        return PermanentEmailError(summary)

    def send(self, message: EmailMessage) -> SendResult:
        body: dict[str, object] = {
            "from": self.from_address,
            "to": [message.to],
            "subject": message.subject,
            "text": message.text,
        }
        data = self._request("POST", "/emails", body, {"Idempotency-Key": message.idempotency_key})
        provider_id = data.get("id")
        if not isinstance(provider_id, str):
            raise TransientEmailError("Resend response had no message id")
        log.info("resend accepted email to=%s id=%s", mask_email(message.to), provider_id)
        return SendResult("accepted", provider_id)

    def last_event(self, provider_message_id: str) -> str | None:
        data = self._request("GET", f"/emails/{provider_message_id}", None, {})
        event = data.get("last_event")
        return event if isinstance(event, str) else None


def get_sender(settings: Settings) -> EmailSender:
    if settings.email_mode == "resend":
        if settings.resend_api_key is None:
            raise RuntimeError("CAMPUSFLOW_EMAIL_MODE=resend needs CAMPUSFLOW_RESEND_API_KEY")
        return ResendSender(settings.resend_api_key.get_secret_value(), settings.email_from)
    return CaptureSender(settings.email_capture_dir)
