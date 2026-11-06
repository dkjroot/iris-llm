"""Configure the PC side of Iris Droid without editing Python source."""

from __future__ import annotations

import json
from pathlib import Path

from iris_settings import Settings, load_settings

ROOT = Path(__file__).resolve().parent


def choose(prompt: str, choices: list[str], default: str) -> str:
    while True:
        answer = input(f"{prompt} ({', '.join(choices)}) [{default}]: ").strip()
        if not answer:
            return default
        if answer in choices:
            return answer
        print("Choose one of the names shown above.")


def choose_kokoro_speed(default: float) -> float:
    while True:
        answer = input(f"Talking speed (0.5-2.0; 1.0 = normal, try 1.3) [{default}]: ").strip()
        if not answer:
            return default
        try:
            speed = float(answer)
        except ValueError:
            pass
        else:
            if 0.5 <= speed <= 2.0:
                return speed
        print("Choose a number from 0.5 to 2.0.")


def main() -> int:
    path = ROOT / "local-config.json"
    try:
        print("Let's set up the PC server for Iris Droid.")
        print(
            "The laptop or Pi looks after the microphone, listening mode and speaker. "
            "You choose its listening mode when you launch the client, not here."
        )
        print(
            "Whisper recognises your speech on the PC; "
            "Kokoro makes the voice sent back to the client."
        )
        previous = load_settings(path if path.exists() else None)
        data = previous.model_dump(mode="json")
        data["base_url"] = (
            input(f"Chat base URL [{previous.base_url}]: ").strip() or previous.base_url
        )
        data["model"] = input(f"Model name [{previous.model}]: ").strip() or previous.model
        data["model_context_tokens"] = int(
            input(
                "Model context window in tokens (match your host) "
                f"[{previous.model_context_tokens}]: "
            ).strip()
            or previous.model_context_tokens
        )
        data["stt_engine"] = choose(
            "Speech recognition on the PC (whisper is the usual local choice)",
            ["whisper", "wav2vec2", "google"],
            previous.stt_engine,
        )
        # The other TTS adapters play on the PC; they cannot return droid audio.
        data["tts_engine"] = "kokoro"
        from iris_tts_engines.kokoro_offline import RECOMMENDED_VOICES

        print("Droid speech output uses Kokoro. Let's choose her voice.")
        for name, description in RECOMMENDED_VOICES.items():
            print(f"  {name}: {description}")
        choices = list(dict.fromkeys([*RECOMMENDED_VOICES, previous.kokoro_voice]))
        data["kokoro_voice"] = choose("Kokoro voice", choices, previous.kokoro_voice)
        data["kokoro_speed"] = choose_kokoro_speed(previous.kokoro_speed)
        print(
            "Remote services means sending chat to another machine or a hosted provider, "
            "or using Google speech recognition."
        )
        print(
            "Leave this off when the model runs on this PC and speech uses Whisper/Kokoro. "
            "Your laptop or Pi can still connect to Iris with it off."
        )
        data["allow_remote"] = (
            choose(
                "Allow outbound remote model/speech services",
                ["yes", "no"],
                "yes" if previous.allow_remote else "no",
            )
            == "yes"
        )
        data["tools_enabled"] = (
            choose(
                "Let the model optionally call Iris skills",
                ["yes", "no"],
                "yes" if previous.tools_enabled else "no",
            )
            == "yes"
        )
        if data["tools_enabled"]:
            data["local_timezone"] = (
                input(f"Local timezone (IANA name) [{previous.local_timezone}]: ").strip()
                or previous.local_timezone
            )
            print(
                "The clock skill runs locally. Weather uses Open-Meteo online; "
                "it receives the configured town/coordinates, not your conversation or audio."
            )
            data["weather_enabled"] = (
                choose(
                    "Enable online weather skill",
                    ["yes", "no"],
                    "yes" if previous.weather_enabled else "no",
                )
                == "yes"
            )
            if data["weather_enabled"]:
                data["weather_location"] = (
                    input(f"Weather town [{previous.weather_location}]: ").strip()
                    or previous.weather_location
                )
                data["weather_country"] = (
                    input(f"Weather country code [{previous.weather_country}]: ").strip()
                    or previous.weather_country
                )
        print(
            "Iris can save conversation text locally on this PC and resume after restarts. "
            "Recordings are never saved. All clients for one owner share a conversation."
        )
        data["memory_enabled"] = (
            choose(
                "Save conversation memory locally",
                ["yes", "no"],
                "yes" if previous.memory_enabled else "no",
            )
            == "yes"
        )
        if data["memory_enabled"]:
            from iris_memory import database_path

            default_path = str(database_path(previous))
            data["memory_database"] = (
                input(f"Memory database [{default_path}]: ").strip() or default_path
            )
            data["memory_owner"] = (
                input(f"Memory owner [{previous.memory_owner}]: ").strip() or previous.memory_owner
            )
            print(
                "Optional semantic recall uses a small local model prepared separately on this PC."
            )
            data["memory_semantic_enabled"] = (
                choose(
                    "Enable local semantic recall",
                    ["yes", "no"],
                    "yes" if previous.memory_semantic_enabled else "no",
                )
                == "yes"
            )
        else:
            data["memory_semantic_enabled"] = False
        settings = Settings.model_validate(data)
        path.write_text(
            json.dumps(settings.model_dump(mode="json"), indent=4) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print("Saved", path)
        if settings.memory_semantic_enabled:
            print(
                "Semantic recall also needs the [memory] extra and: "
                "python iris.py memory download-model"
            )
        print("Next: python iris.py --download-models, then python iris.py --check")
        print(
            "Set IRIS_DROID_TOKEN in both terminals, then start the PC service with "
            "python iris.py server"
        )
        print("For a client on another machine, add --host 0.0.0.0 to the server command.")
        print(
            "That listens on all IPv4 interfaces, including your LAN. "
            "The README explains network access and how to connect the client."
        )
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nConfiguration cancelled; nothing saved.")
        return 0
    except (ValueError, OSError) as error:
        print(f"Couldn't save configuration: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
