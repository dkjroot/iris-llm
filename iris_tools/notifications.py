"""Pushover delivery shared by the chat skill and future sensor alert rules."""

from __future__ import annotations

import time
from threading import Lock

import httpx
from pydantic import BaseModel, ConfigDict, Field

from iris_settings import Settings


class NotificationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=1024, description="Notification text.")
    title: str = Field(default="Iris", min_length=1, max_length=250)


class NotificationSender:
    """One bounded attempt per call; acceptance does not prove phone delivery.

    Keep one sender for a sensor rule runner. Its lock serialises requests and
    its five-second cooldown also applies after failures. There is no automatic
    retry: a lost response could mean that Pushover already accepted the message.
    """

    def __init__(self, settings: Settings, client: httpx.Client) -> None:
        self.settings = settings
        self.client = client
        self._lock = Lock()
        self._next_attempt = 0.0

    def send(self, notification: NotificationArguments) -> dict[str, object]:
        if not self.settings.notifications_enabled:
            return {"error": "Push notifications are disabled."}
        token = self.settings.pushover_api_token
        user = self.settings.pushover_user_key
        if (
            token is None
            or not token.get_secret_value().strip()
            or user is None
            or not user.get_secret_value().strip()
        ):
            return {
                "error": "Pushover is not configured. Set IRIS_PUSHOVER_API_TOKEN and "
                "IRIS_PUSHOVER_USER_KEY in the Iris server environment."
            }
        with self._lock:
            if time.monotonic() < self._next_attempt:
                return {"error": "Notification cooldown is active. Do not retry this turn."}
            self._next_attempt = time.monotonic() + 5
            data = {
                "token": token.get_secret_value(),
                "user": user.get_secret_value(),
                "message": notification.message,
                "title": notification.title,
                "priority": "0",
            }
            if self.settings.pushover_device:
                data["device"] = self.settings.pushover_device
            try:
                response = self.client.post(
                    "https://api.pushover.net/1/messages.json",
                    data=data,
                    timeout=10,
                    follow_redirects=False,
                )
                if 400 <= response.status_code < 500:
                    return {
                        "error": "Pushover rejected the notification. Check the server's "
                        "Pushover credentials, device name and message quota. Do not retry "
                        "this turn."
                    }
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or type(payload.get("status")) is not int:
                    raise ValueError("Invalid Pushover response")
                if payload["status"] != 1:
                    return {
                        "error": "Pushover did not accept the notification. Check its "
                        "configuration and quota. Do not retry this turn."
                    }
            except (httpx.HTTPError, ValueError):
                # Never return exception text or provider bodies: either can contain secrets.
                return {
                    "error": "Could not confirm whether Pushover accepted the notification. "
                    "It may already have been sent. Do not retry this turn."
                }
        return {
            "status": "accepted",
            "service": "Pushover",
            "detail": "Pushover accepted the notification for delivery. "
            "This does not confirm it appeared on the phone or was read.",
        }
