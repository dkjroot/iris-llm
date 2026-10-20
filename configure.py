"""Choose the main pieces, without needing to edit Python source."""

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
        previous = load_settings(path if path.exists() else None)
        data = previous.model_dump(mode="json")
        data["base_url"] = (
            input(f"Chat base URL [{previous.base_url}]: ").strip() or previous.base_url
        )
        data["model"] = input(f"Model name [{previous.model}]: ").strip() or previous.model
        data["stt_engine"] = choose(
            "Speech recognition", ["whisper", "wav2vec2", "google"], previous.stt_engine
        )
        data["tts_engine"] = choose(
            "Speech output", ["pyttsx3", "kokoro", "gtts"], previous.tts_engine
        )
        if data["tts_engine"] == "kokoro":
            from iris_tts_engines.kokoro_offline import RECOMMENDED_VOICES

            for name, description in RECOMMENDED_VOICES.items():
                print(f"  {name}: {description}")
            choices = list(dict.fromkeys([*RECOMMENDED_VOICES, previous.kokoro_voice]))
            data["kokoro_voice"] = choose("Kokoro voice", choices, previous.kokoro_voice)
            data["kokoro_speed"] = choose_kokoro_speed(previous.kokoro_speed)
        data["activation"] = choose(
            "Start listening (space = hold-to-talk on Windows)",
            ["enter", "space", "wake", "continuous"],
            previous.activation,
        )
        data["sounds"] = (
            choose("Feedback sounds", ["yes", "no"], "yes" if previous.sounds else "no") == "yes"
        )
        print("Remote mode allows non-loopback chat endpoints and Google speech services.")
        data["allow_remote"] = (
            choose("Allow remote services", ["yes", "no"], "yes" if previous.allow_remote else "no")
            == "yes"
        )
        settings = Settings.model_validate(data)
        path.write_text(
            json.dumps(settings.model_dump(mode="json"), indent=4) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print("Saved", path)
        if settings.tts_engine == "kokoro":
            print("Next: python iris.py --download-voices, then python iris.py --test-voice")
        print(
            "Keys belong in IRIS_API_KEY, not this file. More options are in config_template.json."
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
