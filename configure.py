"""Configure the PC side of Iris Droid without editing Python source."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from iris_paths import atomic_json, server_config
from iris_settings import (
    DEFAULT_CHAT_BASE_URL,
    DEFAULT_CHAT_MODEL,
    OPENROUTER_BASE_URL,
    Settings,
    migrate_config,
    read_config,
)


class _FileSettings(Settings):
    """Validate saved server configuration without merging transient IRIS_* env overrides."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


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
        "chatterbox_device",
        "chatterbox_voice",
        "pocket_voice",
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
    "home_service_token": "Set IRIS_HOME_SERVICE_TOKEN in the server environment; "
    "it isn't saved here.",
    "api_key": "Set IRIS_API_KEY in the server environment; it isn't saved here.",
    "pushover_api_token": "Set IRIS_PUSHOVER_API_TOKEN in the server environment; "
    "it isn't saved here.",
    "pushover_user_key": "Set IRIS_PUSHOVER_USER_KEY in the server environment; "
    "it isn't saved here.",
}

ADVANCED_HELP = {
    "home_service_url": "URL of the standalone home automation service",
    "home_service_ca": "PEM certificate to trust for the home automation service",
    "system_prompt": "Iris's instructions and personality",
    "conversation_style_prompt": 'Character\'s conversation style; enter "" to remove the nudge',
    "temperature": "Reply randomness, from 0 to 2",
    "max_tokens": "Maximum tokens in a reply",
    "context_margin_tokens": "Context space reserved for the host's formatting",
    "memory_hierarchy_enabled": "Interpret facts, corrections and topic attention "
    "with a bounded metadata model pass",
    "memory_extraction_model": "Optional model name for background memory summaries and "
    "hierarchy extraction; null uses model",
    "memory_summary_tokens": "Maximum tokens in a memory summary",
    "memory_summary_interval": "Unsummarised exchanges before a checkpoint",
    "memory_summary_chars": "Maximum summary characters in chat context",
    "memory_retrieval_chars": "Maximum retrieved source characters in chat context",
    "history_turns": "Recent whole turns retained in context; 0 disables prompt history",
    "memory_embedding_model_dir": "Prepared semantic model directory; null uses the default",
    "memory_semantic_min_score": "Minimum semantic similarity, from 0 to 1",
    "memory_context_chars": "Maximum restored conversation characters",
    "timeout": "Model request timeout in seconds",
    "turn_timeout": "Overall server turn deadline in seconds",
    "upload_timeout": "Request body upload timeout in seconds",
    "queue_timeout": "Maximum wait in the server turn queue in seconds",
    "max_pending_turns": "Maximum concurrent uploaded/queued turns on the server",
    "trace": "Print model/tool diagnostics, including conversation text",
    "reply_encoding": "Reply text encoding: text, html or double-html",
    "chat_template_kwargs": "Host template options as a JSON object",
    "chat_request_options": "Additional model request options as a JSON object",
    "whisper_model": "Whisper model name or local path",
    "whisper_vad_filter": "Filter silence in Whisper STT; disable if client VAD is already active",
    "whisper_beam_size": "Whisper beam search width (1 is fastest greedy decoding, 5 is default)",
    "device": "Speech recognition device: cpu, cuda or auto",
    "compute_type": "Whisper compute type, such as int8 or float16",
    "language": "Recognition language code; null enables automatic detection",
    "tokenizer_model": "Wav2vec2 tokenizer model name or path",
    "ctc_model": "Wav2vec2 speech model name or path",
    "kokoro_model_dir": "Kokoro model directory; null uses the default",
    "chatterbox_python": "Chatterbox Python interpreter; null uses .venv-chatterbox",
    "chatterbox_model_dir": "Chatterbox Turbo model directory; null uses .cache/chatterbox-turbo",
    "pocket_python": "Pocket TTS Python interpreter; null uses .venv-pocket",
    "pocket_model_dir": "Pocket TTS model directory; null uses .cache/pocket-tts",
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
                candidate = {**data, name: parsed}
                validated = _FileSettings.model_validate(candidate)
            except (ValueError, ValidationError):
                print("That value doesn't match the setting's type or limits. Please try again.")
            else:
                data[name] = validated.model_dump(mode="json")[name]
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


def configure_tts(previous: Settings, data: dict[str, Any]) -> None:
    while True:
        engine = choose(
            "Speech synthesis on the PC", ["kokoro", "chatterbox", "pocket"], previous.tts_engine
        )
        data["tts_engine"] = engine
        if engine in {"chatterbox", "pocket"}:
            from iris_tts_engines.chatterbox import VOICE_DIRECTORY
            from iris_tts_engines.voice_cli import VOICE_NAME

            is_pocket = engine == "pocket"
            suffix = "*.safetensors" if is_pocket else "*.pt"
            choices = sorted(
                f"file:{path.stem}"
                for path in VOICE_DIRECTORY.glob(suffix)
                if path.is_file() and VOICE_NAME.fullmatch(path.stem)
            )
            if is_pocket:
                if not choices:
                    print(
                        "Prepare a Pocket voice first. "
                        "Run python iris.py voice --engine pocket prepare WAV NAME."
                    )
                    continue
                default = f"file:{previous.pocket_voice}"
                voice = choose(
                    "Pocket TTS voice", choices, default if default in choices else choices[0]
                )
                data["pocket_voice"] = voice.removeprefix("file:")
                return
            data["chatterbox_device"] = choose(
                "Chatterbox Turbo device", ["cuda", "cpu"], previous.chatterbox_device
            )
            print("Use the bundled voice or prepare one with python iris.py voice prepare.")
            default = f"file:{previous.chatterbox_voice}"
            selected = choose(
                "Chatterbox Turbo voice",
                ["bundled", *choices],
                default if default in choices else "bundled",
            )
            data["chatterbox_voice"] = (
                selected.removeprefix("file:") if selected != "bundled" else None
            )
            return
        from iris_tts_engines.kokoro_offline import RECOMMENDED_VOICES

        for name, description in RECOMMENDED_VOICES.items():
            print(f"  {name}: {description}")
        choices = list(dict.fromkeys([*RECOMMENDED_VOICES, previous.kokoro_voice]))
        data["kokoro_voice"] = choose("Kokoro voice", choices, previous.kokoro_voice)
        data["kokoro_speed"] = choose_kokoro_speed(previous.kokoro_speed)
        return


def configure_chat(data: dict[str, Any], previous: Settings) -> None:
    connection = choose(
        "Chat connection (custom for a local server or another endpoint)",
        ["custom", "openrouter"],
        "openrouter" if previous.uses_openrouter else "custom",
    )
    if connection == "openrouter":
        data["base_url"] = OPENROUTER_BASE_URL
        print("Find the model ID at https://openrouter.ai/models.")
        print("Set IRIS_API_KEY to your OpenRouter key in the Iris server environment.")
        default_model = previous.model if previous.uses_openrouter else ""
    else:
        default_url = DEFAULT_CHAT_BASE_URL if previous.uses_openrouter else previous.base_url
        data["base_url"] = input(f"Chat base URL [{default_url}]: ").strip() or default_url
        default_model = DEFAULT_CHAT_MODEL if previous.uses_openrouter else previous.model
    model_prompt = f"Model name [{default_model}]: " if default_model else "Model name: "
    while True:
        model = input(model_prompt).strip() or default_model
        if model:
            data["model"] = model
            return
        print("Enter the model ID you want Iris to use.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Configuration file to create or update")
    args = parser.parse_args(argv)
    try:
        path = server_config(args.config, for_write=True)
        assert path is not None
        print("Let's set up the PC server for Iris Droid.")
        print(
            "The laptop or Pi looks after the microphone, listening mode and speaker. "
            "You choose its listening mode when you launch the client, not here."
        )
        print(
            "Whisper recognises your speech on the PC; "
            "The speech synthesizer makes the voice sent back to the client."
        )
        if path.exists():
            try:
                previous: Settings = _FileSettings.model_validate(migrate_config(read_config(path)))
            except (ValueError, ValidationError) as error:
                print(f"Couldn't load existing {path} ({error}); starting from defaults.")
                previous = _FileSettings()
        else:
            previous = _FileSettings()
        data = previous.model_dump(mode="json")
        configure_chat(data, previous)
        while True:
            raw_context = input(
                "Model context window in tokens (match your host) "
                f"[{previous.model_context_tokens}]: "
            ).strip()
            if not raw_context:
                data["model_context_tokens"] = previous.model_context_tokens
                break
            try:
                context_tokens = int(raw_context)
                if 2048 <= context_tokens <= 2000000:
                    data["model_context_tokens"] = context_tokens
                    break
            except ValueError:
                pass
            print("Enter a whole number of tokens between 2048 and 2000000.")
        data["stt_engine"] = choose(
            "Speech recognition on the PC (whisper is the usual local choice)",
            ["whisper", "wav2vec2", "google"],
            previous.stt_engine,
        )
        configure_tts(previous, data)
        print(
            "Remote services means sending chat to another machine or a hosted provider, "
            "or using Google speech recognition."
        )
        print(
            "Leave this off when the chat model and speech processing run on this PC. "
            "Your laptop or Pi can still connect to Iris with it off."
        )
        data["allow_remote"] = (
            choose(
                "Allow outbound remote model/speech services",
                ["yes", "no"],
                "yes" if previous.allow_remote or data["base_url"] == OPENROUTER_BASE_URL else "no",
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
        settings = _FileSettings.model_validate(data)
        atomic_json(path, settings.model_dump(mode="json"))
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
