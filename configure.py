"""Configure the PC side of Iris Droid without editing Python source."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from iris_settings import Settings, load_settings

ROOT = Path(__file__).resolve().parent

# Everything else in Settings is offered in the advanced section automatically.
# New server settings must not silently become JSON-only knobs.
BASIC_SETTINGS = frozenset(
    {
        "base_url",
        "model",
        "model_context_tokens",
        "stt_engine",
        "tts_engine",
        "kokoro_voice",
        "kokoro_speed",
        "allow_remote",
        "tools_enabled",
        "local_timezone",
        "weather_enabled",
        "weather_location",
        "weather_country",
        "notifications_enabled",
        "pushover_device",
        "memory_enabled",
        "memory_database",
        "memory_owner",
        "memory_semantic_enabled",
        "memory_companion_followups",
        "memory_search_tool",
    }
)
ENVIRONMENT_SETTINGS = {
    "api_key": "Set IRIS_API_KEY in the server environment; it isn't saved here.",
    "pushover_api_token": "Set IRIS_PUSHOVER_API_TOKEN in the server environment; "
    "it isn't saved here.",
    "pushover_user_key": "Set IRIS_PUSHOVER_USER_KEY in the server environment; "
    "it isn't saved here.",
}

ADVANCED_HELP = {
    "system_prompt": "Iris's instructions and personality",
    "conversation_style_prompt": 'Character\'s conversation style; enter "" to remove the nudge',
    "temperature": "Reply randomness, from 0 to 2",
    "max_tokens": "Maximum tokens in a reply",
    "context_margin_tokens": "Context space reserved for the host's formatting",
    "memory_hierarchy_enabled": "Interpret facts, corrections and topic attention "
    "with a bounded metadata model pass",
    "memory_summary_tokens": "Maximum tokens in a memory summary",
    "memory_summary_interval": "Unsummarised exchanges before a checkpoint",
    "memory_summary_chars": "Maximum summary characters in chat context",
    "memory_retrieval_chars": "Maximum retrieved source characters in chat context",
    "history_turns": "Recent whole turns retained in context; 0 disables prompt history",
    "memory_embedding_model_dir": "Prepared semantic model directory; null uses the default",
    "memory_semantic_min_score": "Minimum semantic similarity, from 0 to 1",
    "memory_context_chars": "Maximum restored conversation characters",
    "timeout": "Model request timeout in seconds",
    "trace": "Print model/tool diagnostics, including conversation text",
    "reply_encoding": "Reply text encoding: text, html or double-html",
    "chat_template_kwargs": "Host template options as a JSON object",
    "chat_request_options": "Additional model request options as a JSON object",
    "whisper_model": "Whisper model name or local path",
    "device": "Speech recognition device: cpu, cuda or auto",
    "compute_type": "Whisper compute type, such as int8 or float16",
    "language": "Recognition language code; null enables automatic detection",
    "tokenizer_model": "Wav2vec2 tokenizer model name or path",
    "ctc_model": "Wav2vec2 speech model name or path",
    "kokoro_model_dir": "Kokoro model directory; null uses the default",
    "phrase_time_limit": "Maximum accepted recording length in seconds; match the client too",
}


def configure_advanced(data: dict[str, Any]) -> None:
    print(
        "Advanced settings cover chat, memory budgets, recognition and speech models. "
        "Press Enter to keep a value. Use JSON for numbers, booleans and objects; "
        "use null to reset an optional value. Strings and paths don't need quotes."
    )
    for name, field in Settings.model_fields.items():
        if name in BASIC_SETTINGS or name in ENVIRONMENT_SETTINGS:
            continue
        adapter: TypeAdapter[Any] = TypeAdapter(field.rebuild_annotation())
        schema = adapter.json_schema()
        alternatives = schema.get("anyOf", [schema])
        text_value = any(option.get("type") == "string" for option in alternatives)
        while True:
            default = json.dumps(data[name], ensure_ascii=False)
            help_text = ADVANCED_HELP.get(name, name.replace("_", " "))
            answer = input(f"{name} — {help_text} [{default}]: ").strip()
            if not answer:
                break
            try:
                value = answer if text_value and answer != "null" else json.loads(answer)
                # JSON-quoted strings are accepted too, without quoting paths twice.
                if text_value and answer.startswith('"'):
                    value = json.loads(answer)
                parsed = adapter.validate_python(value)
            except ValueError:
                print("That value doesn't match the setting's type or limits. Please try again.")
            else:
                data[name] = parsed
                break


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
        data["local_timezone"] = (
            input(f"Local timezone (IANA name) [{previous.local_timezone}]: ").strip()
            or previous.local_timezone
        )
        if data["tools_enabled"]:
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
        print("Pushover can send notification text to your phone through its online service.")
        data["notifications_enabled"] = (
            choose(
                "Enable Pushover notifications",
                ["yes", "no"],
                "yes" if previous.notifications_enabled else "no",
            )
            == "yes"
        )
        if data["notifications_enabled"]:
            data["pushover_device"] = (
                input(
                    "Pushover device name (blank means all devices; use - to reset) "
                    f"[{previous.pushover_device}]: "
                ).strip()
                or previous.pushover_device
            )
            if data["pushover_device"] == "-":
                data["pushover_device"] = ""
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
            print(
                "Companion follow-ups can occasionally revisit an unfinished saved topic. "
                "They have cooldowns and a spoken opt-out; "
                "sensitive-topic filtering can make mistakes."
            )
            data["memory_companion_followups"] = (
                choose(
                    "Enable memory-based companion follow-ups",
                    ["yes", "no"],
                    "yes" if previous.memory_companion_followups else "no",
                )
                == "yes"
            )
            if data["tools_enabled"]:
                data["memory_search_tool"] = (
                    choose(
                        "Let the model search saved conversations with a read-only skill",
                        ["yes", "no"],
                        "yes" if previous.memory_search_tool else "no",
                    )
                    == "yes"
                )
        else:
            data["memory_semantic_enabled"] = False
            data["memory_companion_followups"] = False
        if choose("Configure advanced server settings", ["yes", "no"], "no") == "yes":
            configure_advanced(data)
        settings = Settings.model_validate(data)
        path.write_text(
            json.dumps(settings.model_dump(mode="json"), indent=4) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print("Saved", path)
        for explanation in ENVIRONMENT_SETTINGS.values():
            print(explanation)
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
