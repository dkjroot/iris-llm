"""Conversation tools for the standalone home automation service."""

import ssl

import httpx
from pydantic import BaseModel

from iris_settings import Settings

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
    def __init__(self, settings: Settings) -> None:
        self.url = settings.home_service_url
        self.token = settings.home_service_token
        self.ca = settings.home_service_ca

    def execute(self, operation: str, args: BaseModel) -> dict[str, object]:
        if self.url is None or self.token is None:
            return {"error": "The home service is not configured."}
        try:
            verify = ssl.create_default_context(cafile=str(self.ca)) if self.ca else True
            with httpx.Client(verify=verify, trust_env=False, timeout=20) as client:
                response = client.post(
                    f"{self.url}/v1/{operation}",
                    json=args.model_dump(),
                    headers={"Authorization": f"Bearer {self.token.get_secret_value()}"},
                )
            payload = response.json()
        except (OSError, ValueError, httpx.HTTPError):
            return {"error": "The home service did not return a result. Check before retrying."}
        if not isinstance(payload, dict) or not (response.is_success or "error" in payload):
            return {"error": "The home service returned an invalid result."}
        return dict(payload)
