"""Set up the microphone, speaker and listening options for Iris Droid."""

from __future__ import annotations

import argparse
import json

from configure import choose
from iris_droid import client_settings
from iris_droid.client_settings import (
    audio_selector,
    config_options,
    config_values,
    parse_config_value,
    settings_parser,
    validate_client_options,
)
from iris_droid.companion import CompanionTimer
from iris_droid.conversation import WakeConversation
from iris_settings import read_config

BASIC_SETTINGS = (
    "server",
    "activation",
    "microphone",
    "speaker",
    "vad_model",
    "voice_cancel",
    "wake_model",
)


def prompt_setting(action: argparse.Action, data: dict[str, object]) -> None:
    current = data.get(action.dest, action.default)
    description = action.help or action.dest.replace("_", " ").capitalize()
    if isinstance(action.default, bool):
        answer = choose(description, ["yes", "no"], "yes" if current else "no")
        if (answer == "yes") != current:
            data[action.dest] = answer == "yes"
        return
    if action.choices is not None:
        description += f" ({', '.join(map(str, action.choices))})"
    while True:
        answer = input(f"{description} [{json.dumps(current)}]: ").strip()
        if not answer:
            return
        try:
            value: object = answer
            if answer == "null" or action.type in (int, float):
                value = json.loads(answer)
            elif action.type is audio_selector:
                value = audio_selector(answer)
            parse_config_value(action, value)
        except (ValueError, argparse.ArgumentTypeError) as error:
            print(f"{error}. Try again.")
        else:
            data[action.dest] = value
            return


def main() -> int:
    path = client_settings.DEFAULT_CLIENT_CONFIG
    try:
        print("Let's set up the Iris client on this machine.")
        print("Press Enter to keep a value. Enter null to clear an optional setting.")
        print("Keep your connection key in IRIS_DROID_TOKEN.")
        parser = settings_parser()
        options = config_options(parser)
        data = read_config(path) if path.exists() else {}
        config_values(data, options)
        for name in BASIC_SETTINGS:
            prompt_setting(options[name], data)
        if choose("Configure advanced client settings", ["yes", "no"], "no") == "yes":
            for name, action in options.items():
                if name not in BASIC_SETTINGS:
                    prompt_setting(action, data)

        values = config_values(data, options)
        args = parser.parse_args([], namespace=argparse.Namespace(**values))
        validate_client_options(args)
        WakeConversation(args.conversation_timeout)
        CompanionTimer(args.companion_idle_min, args.companion_idle_max)
        if (args.activation == "wake" or args.voice_cancel) and args.wake_model is None:
            raise ValueError(
                "Wake activation and voice cancellation need a local Vosk model folder"
            )
        path.write_text(json.dumps(data, indent=4, allow_nan=False) + "\n", encoding="utf-8")
        print(f"Saved {path.resolve()}")
        print("Start Iris from this directory with python iris.py client.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nConfiguration cancelled. Your settings haven't been saved.")
        return 0
    except (ValueError, OSError) as error:
        print(f"Couldn't save client configuration: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
