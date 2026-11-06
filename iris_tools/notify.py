"""Send one test push from a laptop without starting Iris or loading audio models."""

from __future__ import annotations

import argparse
import getpass
import json

import httpx
from pydantic import SecretStr, ValidationError

from iris_settings import Settings
from iris_tools.notifications import NotificationArguments, NotificationSender


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message", default="Hello from Iris! This is a test from your MacBook.")
    parser.add_argument("--title", default="Iris test")
    parser.add_argument("--device", help="Pushover device name; omit to use all registered devices")
    args = parser.parse_args()
    try:
        notification = NotificationArguments(message=args.message, title=args.title)
        options: dict[str, object] = {"notifications_enabled": True}
        if args.device is not None:
            options["pushover_device"] = args.device
        settings = Settings.model_validate(options)
        if settings.pushover_api_token is None:
            settings.pushover_api_token = SecretStr(
                getpass.getpass("Pushover application API token: ")
            )
        if settings.pushover_user_key is None:
            settings.pushover_user_key = SecretStr(getpass.getpass("Pushover user key: "))
        with httpx.Client() as client:
            result = NotificationSender(settings, client).send(notification)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if "error" in result else 0
    except (KeyboardInterrupt, EOFError):
        print("\nTest cancelled.")
        return 1
    except ValidationError:
        print("Invalid settings or notification. Check the device name and message/title lengths.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
