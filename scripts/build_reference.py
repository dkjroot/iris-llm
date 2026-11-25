"""Build the public settings inventory from declarations, never from local configuration."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from configure import ADVANCED_HELP, ENVIRONMENT_SETTINGS  # noqa: E402
from iris_droid.client_settings import client_parser, settings_parser  # noqa: E402
from iris_home.service import Config, MatterConfig  # noqa: E402
from iris_settings import Settings  # noqa: E402

BASIC_HELP = {
    "base_url": "Model API root, including /v1; localhost unless allow_remote is enabled.",
    "model": "Exact model identifier expected by that API.",
    "model_context_tokens": "Model context window; match the allocation in the model host.",
    "stt_engine": "Speech recogniser: local whisper/wav2vec2 or explicitly online google.",
    "tts_engine": "Local speech engine: kokoro, chatterbox or pocket.",
    "kokoro_voice": "Voice identifier from the installed Kokoro pack.",
    "kokoro_speed": "Speech speed multiplier, not model temperature.",
    "chatterbox_device": "Isolated Chatterbox execution device.",
    "chatterbox_voice": "Prepared voice basename; null selects the bundled voice.",
    "pocket_voice": "Required prepared Pocket voice basename; null cannot start this engine.",
    "allow_remote": "Permit a non-loopback chat endpoint or Google recognition; not client access.",
    "tools_enabled": "Expose configured skills to a model that supports tools.",
    "local_timezone": "IANA timezone for clock, memory dates and conversation context.",
    "weather_enabled": "Opt in to Open-Meteo requests independently of local model traffic.",
    "weather_location": "Town sent to the weather geocoder.",
    "weather_country": "Two-letter country used to disambiguate that town.",
    "notifications_enabled": "Opt in to explicitly requested online Pushover notifications.",
    "pushover_device": "Optional Pushover destination; empty uses account defaults.",
    "memory_enabled": "Store completed non-private source exchanges; no automatic expiry.",
    "memory_database": "Archive path; null uses the OS user-data location.",
    "memory_owner": "Shared archive/privacy identity for this server, not a client UUID.",
    "memory_semantic_enabled": "Enable prepared embedding retrieval; requires memory_enabled.",
    "memory_companion_followups": "Allow eligible archived topics in companion openers.",
    "memory_search_tool": "Expose read-only archive search when memory and tools permit it.",
    "turn_timeout": "Cooperative total turn budget, including upload and admission, in seconds.",
    "upload_timeout": "Total admitted body-upload allowance in seconds.",
    "queue_timeout": "Maximum wait for the serial brain after upload, in seconds.",
    "max_pending_turns": "Admission slots including uploads, queued turns and the active turn.",
}


def value(value: object) -> str:
    return (
        json.dumps(value, ensure_ascii=False, default=str).replace("|", "&#124;").replace("\n", " ")
    )


def schema_type(schema: dict) -> str:
    if "enum" in schema:
        return ", ".join(value(item) for item in schema["enum"])
    if "anyOf" in schema:
        return " or ".join(schema_type(item) for item in schema["anyOf"])
    kind = schema.get("type", "object")
    bounds = [
        f"{name}={value(schema[name])}"
        for name in (
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "minLength",
            "maxLength",
            "pattern",
        )
        if name in schema
    ]
    return str(kind) + ("; " + "; ".join(bounds) if bounds else "")


def render() -> str:
    lines = [
        "# Settings and command-line reference",
        "",
        "This page lists declared defaults, not values from your installation. Server",
        "environment variables named `IRIS_<UPPERCASE_SETTING>` override the JSON file.",
        "A missing optional value is written as `null`; it is different from an empty string.",
        "Unknown JSON keys fail validation. Relative paths use the process's working",
        "directory. See [configuration precedence]"
        "(runtime-contracts.md#finding-and-saving-configuration)",
        "and [the technical guide](technical-guide.md) before changing timing or memory budgets.",
        "",
        "The tables are built from declarations with `python scripts/build_reference.py`.",
        "The documentation test checks that they stay in step; this command never reads",
        "your configuration, keys, models or archive.",
        "",
        "## Server JSON and environment",
        "",
        "The same fields are available through `python iris.py configure`. Credentials",
        "are environment-only in the wizard and are excluded from saved JSON. Use",
        "the validation rules together: for example, output tokens plus the context",
        "margin and 1,024 prompt tokens must fit inside the model context window.",
        "",
        "| Key | Default | Type and field limits | Meaning |",
        "| --- | --- | --- | --- |",
    ]
    schema = Settings.model_json_schema()
    for name, field in schema["properties"].items():
        description = (
            ENVIRONMENT_SETTINGS.get(name) or BASIC_HELP.get(name) or ADVANCED_HELP.get(name)
        )
        if not description:
            raise ValueError(f"Explain the new setting: {name}")
        lines.append(
            f"| `{name}` | `{value(field.get('default'))}` | {schema_type(field)} | {description} |"
        )
    lines += [
        "",
        "## Client file and command line",
        "",
        "Saved keys use underscores; command-line flags use hyphens. Explicit flags",
        "override `client_config.json`. The connection key is `IRIS_DROID_TOKEN`, not",
        "a saved client setting. These defaults do not select your actual audio devices.",
        "",
        "| Saved key | Flag | Default | Choices or explanation |",
        "| --- | --- | --- | --- |",
    ]
    settings = settings_parser()
    for action in settings._actions:
        help_text = action.help or action.dest.replace("_", " ")
        choices = "" if action.choices is None else " Choices: " + value(list(action.choices)) + "."
        lines.append(
            f"| `{action.dest}` | `{action.option_strings[0]}` | `{value(action.default)}` | "
            f"{help_text}{choices} |"
        )
    saved = {a.dest for a in settings._actions}
    lines += [
        "",
        "These switches are command-only and are not saved in the client file:",
        "",
        "| Flag | Meaning |",
        "| --- | --- |",
    ]
    for action in client_parser(settings)._actions:
        if action.dest not in saved:
            lines.append(f"| `{' / '.join(action.option_strings)}` | {action.help} |")
    lines += [
        "",
        "## Home service JSON",
        "",
        "Home configuration is selected with `python -m iris_home.service --config PATH`.",
        "It does not use the server's `IRIS_<SETTING>` overrides. `IRIS_HOME_TOKEN` is",
        "its bearer key; the PC uses the same value as `IRIS_HOME_SERVICE_TOKEN`.",
        "LAN listening requires both TLS paths. Device names and switch addresses must",
        "be unique. See [home setup](home-automation.md) for pairing and an explained example.",
    ]
    for title, model in (("Service fields", Config), ("Each devices entry", MatterConfig)):
        lines += [
            "",
            f"### {title}",
            "",
            "| Key | Default | Type and limits |",
            "| --- | --- | --- |",
        ]
        for name, field in model.model_json_schema()["properties"].items():
            default = value(field["default"]) if "default" in field else "required"
            lines.append(f"| `{name}` | `{default}` | {schema_type(field)} |")
    lines += [
        "",
        "## Other process settings",
        "",
        "| Name | Where and why |",
        "| --- | --- |",
        "| `IRIS_DROID_TOKEN` | Shared bearer key on server, client, phone and Windows updater. "
        "Short keys warn but remain accepted for compatibility. |",
        "| `IRIS_HOME_TOKEN` | Home-service key: at least 32 ASCII letters/digits. |",
        "| `IRIS_PERFORMANCE_LOG=1` | Text-free stage timing on the process where it is set. |",
        "| `IRIS_INSTANCE_ID` | Opaque 32-hex Windows-updater identity; inherited by the launched "
        "server and echoed in authenticated health. |",
        "| `LOCALAPPDATA`, `XDG_DATA_HOME`, `XDG_STATE_HOME` | OS-dependent user data/state roots; "
        "see the path reference below. |",
        "| `IRIS_TEST_EMBEDDING_MODEL_DIR`, `IRIS_TEST_BROWSER_CHANNEL`, "
        "`PLAYWRIGHT_BROWSERS_PATH` | Test-only opt-ins, described in the testing guide. |",
        "",
        "Server listening and web switches are launch options, not server JSON keys:",
        "`--config`, `--host` (127.0.0.1), `--port` (8765), `--web-chat`, `--reasoning`, and",
        "`--web-personalities-file`. Use `python iris.py server --help` for their descriptions.",
        "The updater, memory, voice, notification and display commands have their own",
        "`--help`; [interface reference](interfaces.md) maps each to its guide and tests.",
        "",
        "Use [runtime paths](runtime-contracts.md#prepared-speech-assets-outside-a-checkout),",
        "[memory storage](memory-storage.md) and [installation profiles](install-profiles.md)",
        "for asset locations and external model/runtime variables. Iris does not treat a",
        "`.env` file as an automatically loaded secret store.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = ROOT / "docs/configuration-reference.md"
    content = render()
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            raise SystemExit("Configuration reference is stale; run scripts/build_reference.py")
    else:
        target.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
