"""Pocket-specific contracts with real pipes, synthetic PCM and no model download."""

import base64
import struct
import sys

import numpy as np
import pytest

from iris_settings import PocketSettings
from iris_tts_engines import pocket
from iris_tts_engines.worker import open_worker
from tests.test_phase4_resilience import DONE, worker_file


def audio_frame(pcm):
    return (
        '    print(json.dumps({"status":"audio", "request_id":rid, "rate":24000, '
        f'"samples":{base64.b64encode(pcm).decode()!r}}}), flush=True)\n'
    )


def test_pocket_pcm_has_correct_scale_rate_and_complete_output(tmp_path):
    body = audio_frame(struct.pack("<hh", -32767, 0)) + audio_frame(struct.pack("<h", 32767))
    with open_worker(worker_file(tmp_path, body + DONE), pocket.Pocket, "Synthetic", {}) as worker:
        samples, rate = worker.synthesize("hello")
        assert rate == 24000 and samples.dtype == np.float32
        np.testing.assert_array_equal(samples, [-1.0, 0.0, 1.0])
        assert not worker.failed
    assert worker.process.poll() is not None


@pytest.mark.parametrize("pcm", [b"", b"x"])
def test_invalid_pocket_pcm_retires_worker_before_delivery(tmp_path, pcm):
    with open_worker(
        worker_file(tmp_path, audio_frame(pcm) + DONE), pocket.Pocket, "Synthetic", {}
    ) as worker:
        with pytest.raises(RuntimeError, match="invalid PCM"):
            next(worker.stream("hello"))
        assert worker.failed
    assert worker.process.poll() is not None


def test_pocket_terminal_without_audio_is_not_a_successful_buffered_reply(tmp_path):
    with open_worker(worker_file(tmp_path, DONE), pocket.Pocket, "Synthetic", {}) as worker:
        with pytest.raises(RuntimeError, match="no audio"):
            worker.synthesize("hello")


def test_pocket_preparation_is_required_before_starting_child(tmp_path, monkeypatch):
    monkeypatch.setattr(pocket, "VOICE_DIRECTORY", tmp_path)
    settings = PocketSettings(pocket_python=sys.executable)
    with pytest.raises(ValueError, match="Choose a Pocket TTS voice"), pocket.open_pocket(settings):
        pytest.fail("A missing voice must not start a worker")
    settings.pocket_voice = "not-prepared"
    with pytest.raises(ValueError, match="voice is missing"), pocket.open_pocket(settings):
        pytest.fail("A missing voice file must not start a worker")


def test_pocket_setup_failure_is_visible_and_environment_stays_local(monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setenv("IRIS_HOME_SERVICE_TOKEN", "synthetic-private-token")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setattr(
        pocket.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or SimpleNamespace(returncode=1),
    )
    with pytest.raises(ValueError, match="setup failed"):
        pocket.run_setup(PocketSettings(pocket_python=sys.executable), "download")
    environment = calls[0][1]["env"]
    assert environment["CUDA_VISIBLE_DEVICES"] == ""
    assert "IRIS_HOME_SERVICE_TOKEN" not in environment
