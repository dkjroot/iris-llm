import json
from pathlib import Path

import pytest

import configure_client
from iris_droid.client_settings import parse_client_args


def test_wizard_saves_settings_the_client_can_use(
    monkeypatch: pytest.MonkeyPatch, isolated_client_config: Path
) -> None:
    answers = iter(
        ["http://droid.local:8765", "continuous", "USB Mic", "7", ".cache/vad.onnx", "", "", "no"]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert configure_client.main([]) == 0

    saved = json.loads(isolated_client_config.read_text(encoding="utf-8"))
    assert saved["microphone"] == "USB Mic"
    assert saved["speaker"] == 7
    args = parse_client_args(["--speaker", "USB Audio"])
    assert args.server == "http://droid.local:8765"
    assert args.activation == "continuous"
    assert args.speaker == "USB Audio"
    assert args.vad_model == Path(".cache/vad.onnx")
    assert args.pause_threshold == 1.2


def test_alternate_config_and_diagnostic_mode(isolated_client_config: Path) -> None:
    path = isolated_client_config.with_name("alternate.json")
    path.write_text(
        '{"voice_cancel": true, "wake_model": "vosk", "no_speech_stream": true, "speaker": 7}',
        encoding="utf-8",
    )
    config = ["--config", str(path)]
    assert parse_client_args(config).voice_cancel
    args = parse_client_args([*config, "--test-microphone"])
    assert not args.voice_cancel
    assert args.no_speech_stream
    assert args.speaker == 7
    assert parse_client_args([*config, "--text", "--voice-cancel"]).voice_cancel


@pytest.mark.parametrize("filename", ["client_config.json", "client-config.json"])
def test_screen_off_in_client_config(isolated_client_config: Path, filename: str) -> None:
    path = isolated_client_config.with_name(filename)
    path.write_text('{"face": "signal", "face_idle": "off"}', encoding="utf-8")
    config = [] if path == isolated_client_config else ["--config", str(path)]
    assert parse_client_args(config).face_idle == "off"
    assert parse_client_args([*config, "--face-idle", "snow"]).face_idle == "snow"


@pytest.mark.parametrize(
    ("data", "error"),
    [
        ({"no_speech_stream": "false"}, "no_speech_stream must be bool"),
        ({"activation": "always"}, "activation must be one of"),
        ({"download_vad_model": "vad.onnx"}, "Unknown client setting"),
    ],
)
def test_invalid_config_reports_the_file_and_setting(
    isolated_client_config: Path, data: dict[str, str], error: str
) -> None:
    isolated_client_config.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=error) as failure:
        parse_client_args([])
    assert str(isolated_client_config) in str(failure.value)
