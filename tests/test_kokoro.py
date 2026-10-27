import hashlib
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest

import iris
from iris_settings import Settings
from iris_tts_engines import kokoro_offline as kokoro


@pytest.fixture
def assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Settings, dict[str, bytes]]:
    data = {"kokoro-v1.0.onnx": b"model", "voices-v1.0.bin": b"voices"}
    monkeypatch.setattr(
        kokoro,
        "ASSETS",
        tuple(
            kokoro.Asset(name, len(body), hashlib.sha256(body).hexdigest())
            for name, body in data.items()
        ),
    )
    return Settings(kokoro_model_dir=tmp_path), data


def test_download_verifies_files_and_reuses_complete_cache(assets: tuple) -> None:
    settings, data = assets
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=data[request.url.path.rsplit("/", 1)[1]])

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        kokoro.download_voices(settings, client)
        kokoro.download_voices(settings, client)
    assert len(requests) == 2
    for name, body in data.items():
        assert (settings.kokoro_model_dir / name).read_bytes() == body
    assert not list(settings.kokoro_model_dir.glob(".kokoro-*"))


@pytest.mark.parametrize("mode", ["checksum", "oversize", "http", "interrupted"])
def test_failed_download_preserves_previous_file_and_removes_partial(
    assets: tuple,
    mode: str,
) -> None:
    settings, _ = assets
    target = settings.kokoro_model_dir / "kokoro-v1.0.onnx"
    target.write_bytes(b"older")

    def handle(request: httpx.Request) -> httpx.Response:
        if mode == "interrupted":
            raise httpx.ReadError("disconnected", request=request)
        return httpx.Response(
            503 if mode == "http" else 200,
            content=b"wrong" if mode == "checksum" else b"too many bytes",
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError):
            kokoro.download_voices(settings, client)
    assert target.read_bytes() == b"older"
    assert not list(settings.kokoro_model_dir.glob(".kokoro-*"))


def test_missing_files_fail_before_constructing_backend(tmp_path: Path) -> None:
    with patch.object(kokoro, "load_backend") as backend:
        with pytest.raises(ValueError, match="--download-voices"):
            kokoro.TTS(Settings(kokoro_model_dir=tmp_path))
        backend.assert_not_called()


@pytest.mark.parametrize("voice,language", [("af_heart", "en-us"), ("bf_emma", "en-gb")])
def test_synthesis_keeps_onset_and_never_opens_a_speaker(
    assets: tuple, voice: str, language: str
) -> None:
    settings, data = assets
    for name, body in data.items():
        (settings.kokoro_model_dir / name).write_bytes(body)
    settings.kokoro_voice = voice
    backend = Mock(get_voices=Mock(return_value=[voice]))
    audio = np.concatenate([np.zeros(1000), np.full(100, 0.1), np.zeros(1000)]).astype(np.float32)
    backend.create.return_value = (audio, 1000)
    with (
        patch.object(kokoro, "load_backend", return_value=backend),
        patch("sounddevice.play") as play,
    ):
        tts = kokoro.TTS(settings)
        for text in ["Hello, I'm Iris.", "Another reply."]:
            samples, rate = tts.synthesize(text)
            assert rate == 1000 and len(samples) == 430
        assert backend.create.call_count == 2
        assert backend.create.call_args.kwargs == {
            "voice": voice,
            "speed": 1.0,
            "lang": language,
            "trim": False,
        }
        play.assert_not_called()


def test_unsupported_pack_voice_is_actionable(assets: tuple) -> None:
    settings, data = assets
    for name, body in data.items():
        (settings.kokoro_model_dir / name).write_bytes(body)
    with patch.object(kokoro, "load_backend", return_value=Mock(get_voices=lambda: [])):
        with pytest.raises(ValueError, match="Try af_heart or bf_emma"):
            kokoro.TTS(settings)


def test_quiet_opening_consonant_survives_trim() -> None:
    samples = np.zeros(2000, dtype=np.float32)
    samples[850:1000] = 0.000001
    samples[1000:1100] = 0.2
    result = kokoro.prepare_audio(samples, 1000)
    np.testing.assert_array_equal(result, samples[750:1180])


@pytest.mark.parametrize(
    "samples,rate",
    [
        (np.array([], dtype=np.float32), 24000),
        (np.array([np.nan], dtype=np.float32), 24000),
        (np.zeros(100, dtype=np.float32), 24000),
        (np.ones(100, dtype=np.float32), 0),
    ],
)
def test_invalid_audio_is_not_sent_to_speakers(samples: np.ndarray, rate: int) -> None:
    with pytest.raises(ValueError):
        kokoro.prepare_audio(samples, rate)


def test_list_voices_does_not_load_models_or_audio(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(kokoro, "load_backend") as backend:
        assert iris.main(["--list-voices"]) == 0
        backend.assert_not_called()
    output = capsys.readouterr().out
    assert "af_heart" in output and "bf_emma" in output and "bm_george" in output


def test_backend_forces_cpu_without_mutating_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("phonemizer")
    monkeypatch.setattr(logger, "filters", [])
    session = Mock()
    runtime = SimpleNamespace(
        InferenceSession=Mock(return_value=session), disable_telemetry_events=Mock()
    )
    model = Mock(from_session=Mock(return_value=Mock()))
    with patch.dict(
        sys.modules, {"onnxruntime": runtime, "kokoro_onnx": SimpleNamespace(Kokoro=model)}
    ):
        result = kokoro.load_backend(tmp_path / "model.onnx", tmp_path / "voices.bin")
    runtime.InferenceSession.assert_called_once_with(
        str(tmp_path / "model.onnx"), providers=["CPUExecutionProvider"]
    )
    runtime.disable_telemetry_events.assert_called_once()
    assert result is model.from_session.return_value
    logger.warning("words count mismatch on %s%% of the lines (%s/%s)", 1000.0, 10, 1)
    logger.warning("Another pronunciation warning")
    logger.error("Speech failed")
    assert [record.getMessage() for record in caplog.records] == [
        "Another pronunciation warning",
        "Speech failed",
    ]


def test_voice_download_command_dispatches_without_loading_backend() -> None:
    with (
        patch.object(kokoro, "download_voices") as download,
        patch.object(kokoro, "load_backend") as backend,
    ):
        assert iris.main(["--download-voices"]) == 0
    download.assert_called_once()
    backend.assert_not_called()
