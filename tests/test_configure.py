import json
from pathlib import Path
from unittest.mock import patch

import pytest

import configure
from iris_settings import load_settings


def test_documented_kobold_whisper_choices_produce_a_usable_config(tmp_path: Path) -> None:
    with (
        patch.object(configure, "ROOT", tmp_path),
        patch("builtins.input", side_effect=["", "koboldcpp/gemma-model", "", "", "", "", ""]),
    ):
        assert configure.main() == 0
    path = tmp_path / "local-config.json"
    settings = load_settings(path)
    assert settings.base_url == "http://127.0.0.1:5001/v1"
    assert settings.model == "koboldcpp/gemma-model"
    assert settings.stt_engine == "whisper"
    assert settings.device == "cpu" and settings.compute_type == "int8"
    assert settings.activation == "enter" and settings.tts_engine == "pyttsx3"
    assert not settings.allow_remote
    assert "api_key" not in json.loads(path.read_text(encoding="utf-8"))


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
        patch("builtins.input", side_effect=["", "", "", "kokoro", "bf_emma", "1.3", "", "", ""]),
    ):
        assert configure.main() == 0
    settings = load_settings(tmp_path / "local-config.json")
    assert settings.tts_engine == "kokoro" and settings.kokoro_voice == "bf_emma"
    assert settings.kokoro_speed == 1.3
    assert "Emma - British female" in capsys.readouterr().out


def test_kokoro_speed_reprompts_for_invalid_values_and_keeps_existing_default() -> None:
    with patch("builtins.input", side_effect=["quick", "nan", "inf", "0.4", "2.1", ""]):
        assert configure.choose_kokoro_speed(1.3) == 1.3
