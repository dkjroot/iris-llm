"""Conversation tools for the standalone home automation service."""

import ssl
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

import httpx
from pydantic import BaseModel

from iris_settings import Settings
from iris_turn import remaining_timeout

HOME_TOOLS = {
    "list_home_devices": ("list", "List the configured home devices and their configured meters."),
    "get_home_state": ("status", "Read the current on/off state of a configured home device."),
    "set_home_power": ("switch", "Turn a configured device on or off on the user's request."),
    "read_home_meter": (
        "meter",
        "Read a device meter. Power means current draw; energy means cumulative usage.",
    ),
    "get_home_schedule": ("schedule", "Read a device's weekly schedule and timezone."),
    "save_home_schedule": (
        "save-schedule",
        "Replace a device's weekly schedule on the user's request. Read it first, use its "
        "current revision, and carry over every rule the user wants to keep. An empty list "
        "clears the schedule. Use local 24-hour times.",
    ),
}


class HomeClient:
    def __init__(self, settings: Settings, *, persistent: bool = False) -> None:
        self.url = settings.home_service_url
        self.token = settings.home_service_token
        self.ca = settings.home_service_ca
        self.persistent = persistent
        self._client: httpx.Client | None = None
        self._lock = Lock()

    def close(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()

    @contextmanager
    def transport(self) -> Iterator[httpx.Client]:
        if not self.persistent:
            verify = ssl.create_default_context(cafile=str(self.ca)) if self.ca else True
            with httpx.Client(verify=verify, trust_env=False) as client:
                yield client
            return
        with self._lock:
            if self._client is None:
                verify = ssl.create_default_context(cafile=str(self.ca)) if self.ca else True
                self._client = httpx.Client(verify=verify, trust_env=False)
            client = self._client
        yield client

    def execute(self, operation: str, args: BaseModel) -> dict[str, object]:
        if self.url is None or self.token is None:
            return {"error": "The home service is not configured."}
        try:
            with self.transport() as client:
                response = client.post(
                    f"{self.url}/v1/{operation}",
                    json=args.model_dump(),
                    timeout=remaining_timeout(20),
                    headers={"Authorization": f"Bearer {self.token.get_secret_value()}"},
                )
            payload = response.json()
        except (OSError, ValueError, httpx.HTTPError):
            return {"error": "The home service did not return a result. Check before retrying."}
        if not isinstance(payload, dict) or not (response.is_success or "error" in payload):
            return {"error": "The home service returned an invalid result."}
        return dict(payload)
