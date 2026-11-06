import json
from pathlib import Path
from unittest.mock import patch

import pytest

import configure
from iris_settings import load_settings


def test_default_wizard_produces_droid_config_without_desktop_prompts(tmp_path: Path) -> None:
    prompts = []

    def answer(prompt: str) -> str:
        prompts.append(prompt)
        return "koboldcpp/gemma-model" if prompt.startswith("Model name") else ""

    with (
        patch.object(configure, "ROOT", tmp_path),
        patch("builtins.input", side_effect=answer),
    ):
        assert configure.main() == 0
    path = tmp_path / "local-config.json"
    settings = load_settings(path)
    assert settings.base_url == "http://127.0.0.1:5001/v1"
    assert settings.model == "koboldcpp/gemma-model"
    assert settings.stt_engine == "whisper"
    assert settings.device == "cpu" and settings.compute_type == "int8"
    assert settings.tts_engine == "kokoro"
    assert not settings.allow_remote
    assert "api_key" not in json.loads(path.read_text(encoding="utf-8"))
    assert len(prompts) == 13
    assert not any("Start listening" in prompt or "Feedback sounds" in prompt for prompt in prompts)


def test_cancelled_configuration_preserves_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "local-config.json"
    original = '{"model": "existing"}\n'
    path.write_text(original, encoding="utf-8")
    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", side_effect=EOFError):
        assert configure.main() == 0
    assert path.read_text(encoding="utf-8") == original


def test_kokoro_wizard_offers_named_voices_and_saves_selection(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with (
        patch.object(configure, "ROOT", tmp_path),
        patch(
            "builtins.input",
            side_effect=["", "", "", "", "bf_emma", "1.3", "", "", "", "", "", "", ""],
        ),
    ):
        assert configure.main() == 0
    settings = load_settings(tmp_path / "local-config.json")
    assert settings.tts_engine == "kokoro" and settings.kokoro_voice == "bf_emma"
    assert settings.kokoro_speed == 1.3
    assert "Emma - British female" in capsys.readouterr().out


def test_existing_config_switches_to_droid_tts_and_preserves_other_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "local-config.json"
    path.write_text(
        json.dumps(
            {
                "tts_engine": "pyttsx3",
                "activation": "space",
                "sounds": False,
                "kokoro_voice": "bf_emma",
                "kokoro_speed": 1.3,
                "base_url": "https://example.com/v1",
                "allow_remote": True,
                "history_turns": 5,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("IRIS_API_KEY", "test-secret-that-must-not-be-saved")
    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", return_value=""):
        assert configure.main() == 0
    settings = load_settings(path)
    assert settings.tts_engine == "kokoro"
    assert settings.kokoro_voice == "bf_emma" and settings.kokoro_speed == 1.3
    assert "activation" not in settings.model_dump() and "sounds" not in settings.model_dump()
    assert settings.allow_remote and settings.history_turns == 5
    assert settings.base_url == "https://example.com/v1"
    assert "test-secret-that-must-not-be-saved" not in path.read_text(encoding="utf-8")
    output = capsys.readouterr().out
    assert "laptop or Pi can still connect" in output
    assert "python iris.py server" in output
    assert "test-secret-that-must-not-be-saved" not in output


def test_kokoro_speed_reprompts_for_invalid_values_and_keeps_existing_default() -> None:
    with patch("builtins.input", side_effect=["quick", "nan", "inf", "0.4", "2.1", ""]):
        assert configure.choose_kokoro_speed(1.3) == 1.3


def test_wizard_configures_optional_online_weather_without_enabling_remote_chat(tmp_path):
    def answer(prompt):
        if prompt.startswith("Enable online weather"):
            return "yes"
        if prompt.startswith("Weather town"):
            return "Edinburgh"
        return ""

    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", side_effect=answer):
        assert configure.main() == 0
    settings = load_settings(tmp_path / "local-config.json")
    assert settings.tools_enabled and settings.weather_enabled
    assert settings.weather_location == "Edinburgh" and settings.weather_country == "GB"
    assert settings.local_timezone == "Europe/London" and not settings.allow_remote


def test_wizard_configures_notifications_without_saving_keys(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IRIS_PUSHOVER_API_TOKEN", "private-app-token")
    monkeypatch.setenv("IRIS_PUSHOVER_USER_KEY", "private-user-key")

    def answer(prompt):
        if prompt.startswith("Enable Pushover"):
            return "yes"
        if prompt.startswith("Pushover device"):
            return "iphone"
        return ""

    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", side_effect=answer):
        assert configure.main() == 0
    path = tmp_path / "local-config.json"
    settings = load_settings(path)
    assert settings.notifications_enabled and settings.pushover_device == "iphone"
    assert not settings.allow_remote
    assert "private-" not in path.read_text() + capsys.readouterr().out
    assert "pushover_api_token" not in json.loads(path.read_text())
    assert "pushover_user_key" not in json.loads(path.read_text())


@pytest.mark.parametrize("enabled", [False, True])
def test_memory_setup_offers_followups_and_search_and_preserves_defaults(tmp_path, enabled):
    path = tmp_path / "local-config.json"
    path.write_text(
        json.dumps(
            {
                "memory_enabled": True,
                "memory_companion_followups": enabled,
                "memory_search_tool": enabled,
            }
        )
    )
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return ""

    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", side_effect=answer):
        assert configure.main() == 0
    settings = load_settings(path)
    assert settings.memory_companion_followups is enabled
    assert settings.memory_search_tool is enabled
    assert any(p.startswith("Enable memory-based companion follow-ups") for p in prompts)
    assert any(p.startswith("Let the model search saved conversations") for p in prompts)


def test_memory_followups_can_be_enabled_in_wizard(tmp_path):
    def answer(prompt):
        if prompt.startswith(("Save conversation memory", "Enable memory-based companion")):
            return "yes"
        if prompt.startswith("Let the model search saved conversations"):
            return "no"
        return ""

    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", side_effect=answer):
        assert configure.main() == 0
    settings = load_settings(tmp_path / "local-config.json")
    assert settings.memory_enabled and settings.memory_companion_followups
    assert not settings.memory_search_tool


def test_advanced_setup_exposes_all_remaining_server_fields_and_saves_typed_values(tmp_path):
    from iris_settings import Settings

    prompts = []
    answers = {
        "max_tokens": "400",
        "temperature": "0.4",
        "history_turns": "6",
        "phrase_time_limit": "90",
        "language": "null",
        "trace": "true",
        "whisper_model": "small.en",
        "reply_encoding": "html",
        "chat_template_kwargs": '{"enable_thinking":false}',
        "chat_request_options": '{"top_p":0.8}',
        "kokoro_model_dir": str(tmp_path / "speech models"),
        "memory_embedding_model_dir": str(tmp_path / "semantic models"),
    }

    def answer(prompt):
        prompts.append(prompt)
        if prompt.startswith("Configure advanced server settings"):
            return "yes"
        return answers.get(prompt.split(" — ")[0], "")

    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", side_effect=answer):
        assert configure.main() == 0
    offered = {p.split(" — ")[0] for p in prompts if " — " in p}
    assert (
        offered
        == Settings.model_fields.keys()
        - configure.BASIC_SETTINGS
        - configure.ENVIRONMENT_SETTINGS.keys()
    )
    settings = load_settings(tmp_path / "local-config.json")
    assert settings.max_tokens == 400 and settings.temperature == 0.4
    assert settings.history_turns == 6 and settings.phrase_time_limit == 90
    assert settings.language is None and settings.trace
    assert settings.whisper_model == "small.en" and settings.reply_encoding == "html"
    assert settings.chat_template_kwargs == {"enable_thinking": False}
    assert settings.chat_request_options == {"top_p": 0.8}
    assert settings.kokoro_model_dir == tmp_path / "speech models"
    assert settings.memory_embedding_model_dir == tmp_path / "semantic models"
    assert "api_key" not in json.loads((tmp_path / "local-config.json").read_text())


def test_advanced_setup_reprompts_for_bad_types_bounds_and_json(tmp_path):
    values = {
        "max_tokens": iter(["-1", "many", "500"]),
        "device": iter(["invalid-gpu", "cpu"]),
        "chat_request_options": iter(["[", "[]", '{"top_p":0.8}']),
    }

    def answer(prompt):
        if prompt.startswith("Configure advanced server settings"):
            return "yes"
        name = prompt.split(" — ")[0]
        return next(values[name]) if name in values else ""

    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", side_effect=answer):
        assert configure.main() == 0
    settings = load_settings(tmp_path / "local-config.json")
    assert settings.max_tokens == 500 and settings.device == "cpu"
    assert settings.chat_request_options == {"top_p": 0.8}


def test_disabling_memory_turns_off_followups_and_keeps_timezone_available_without_tools(tmp_path):
    path = tmp_path / "local-config.json"
    path.write_text(json.dumps({"memory_enabled": True, "memory_companion_followups": True}))
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        if prompt.startswith(("Save conversation memory", "Let the model optionally")):
            return "no"
        if prompt.startswith("Local timezone"):
            return "America/New_York"
        return ""

    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", side_effect=answer):
        assert configure.main() == 0
    settings = load_settings(path)
    assert not settings.memory_enabled and not settings.memory_companion_followups
    assert not settings.tools_enabled and settings.local_timezone == "America/New_York"
    assert not any(p.startswith("Enable memory-based companion") for p in prompts)


def test_cancelling_advanced_configuration_keeps_file_unchanged(tmp_path):
    path = tmp_path / "local-config.json"
    original = '{"model":"existing"}\n'
    path.write_text(original)

    def answer(prompt):
        if prompt.startswith("Configure advanced server settings"):
            return "yes"
        if " — " in prompt:
            raise EOFError
        return ""

    with patch.object(configure, "ROOT", tmp_path), patch("builtins.input", side_effect=answer):
        assert configure.main() == 0
    assert path.read_text() == original
