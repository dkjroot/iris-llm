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
    assert len(prompts) == 6
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
        patch("builtins.input", side_effect=["", "", "", "bf_emma", "1.3", ""]),
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
