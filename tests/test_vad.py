"""Speech segmentation and model preparation without a microphone or downloads."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest
import speech_recognition as sr

import iris
from iris_droid.client import record_turn
from iris_droid.protocol import TurnReply
from iris_droid.vad import FRAME_BYTES, RATE, download_model, listen_for_speech


def source_for(pcm, rate=16000, chunk=512):
    position = 0

    def read(frames):
        nonlocal position
        data = pcm[position : position + frames * 2]
        position += len(data)
        return data

    return SimpleNamespace(SAMPLE_RATE=rate, CHUNK=chunk, stream=Mock(read=Mock(side_effect=read)))


def detector_for(probabilities):
    return Mock(probability=Mock(side_effect=probabilities))


def test_loud_non_speech_and_short_false_starts_expire_without_a_turn():
    source = source_for(b"\xff\x7f" * RATE * 2)
    detector = detector_for([0.1, 0.9, 0.1, 0.9] * 20)
    with pytest.raises(sr.WaitTimeoutError):
        listen_for_speech(source, detector, timeout=1, limit=30, pause=0.5, clock=lambda: 0)
    assert detector.probability.call_count <= 32
    detector.reset.assert_called_once()


def test_speech_keeps_pre_roll_and_ends_despite_loud_non_speech():
    frames = [np.full(512, i, dtype="<i2").tobytes() for i in range(50)]
    source = source_for(b"".join(frames))
    detector = detector_for([0.1] * 20 + [0.9] * 5 + [0.1] * 25)
    result = listen_for_speech(source, detector, timeout=5, limit=30, pause=0.5, clock=lambda: 0)
    # Confirmed speech begins at frame 20; retain seven preceding frames, all
    # speech, and the 16 windows that establish a half-second non-speech pause.
    assert result == b"".join(frames[13:41])
    assert source.stream.read.call_count == 41


def test_brief_pauses_and_quieter_speech_do_not_split_a_turn():
    probabilities = [0.9] * 5 + [0.1] * 5 + [0.4] * 5 + [0.1] * 16
    source = source_for(b"\x01\x00" * 512 * 40)
    result = listen_for_speech(
        source, detector_for(probabilities), timeout=5, limit=30, pause=0.5, clock=lambda: 0
    )
    assert len(result) == len(probabilities) * FRAME_BYTES


@pytest.mark.parametrize("rate", [8000, 16000, 44100, 48000])
def test_stateful_resampling_is_independent_of_native_read_size(rate):
    native = (np.sin(np.arange(rate * 2) * 0.03) * 10000).astype("<i2").tobytes()
    results = []
    for chunk in [256, 8192, 16384]:
        detector = detector_for([0.1] * 5 + [0.9] * 5 + [0.1] * 60)
        results.append(
            listen_for_speech(
                source_for(native, rate, chunk),
                detector,
                timeout=5,
                limit=30,
                pause=0.5,
                clock=lambda: 0,
            )
        )
        assert all(len(call.args[0]) == FRAME_BYTES for call in detector.probability.call_args_list)
    assert results[0] == results[1] == results[2]


def test_continuous_speech_is_bounded_by_the_turn_limit():
    source = source_for(b"\x01\x00" * RATE * 2)
    result = listen_for_speech(
        source, detector_for([0.9] * 80), timeout=1, limit=0.5, pause=0.5, clock=lambda: 0
    )
    assert len(result) == RATE  # Exactly half a second, including pre-roll.


def test_expired_wall_clock_deadline_does_not_read_another_buffer():
    source = source_for(b"\x01\x00" * RATE)
    with pytest.raises(sr.WaitTimeoutError):
        listen_for_speech(
            source,
            detector_for([]),
            timeout=1,
            limit=30,
            pause=0.5,
            clock=Mock(side_effect=[0, 2]),
        )
    source.stream.read.assert_not_called()


def test_device_overflow_is_not_swallowed():
    source = source_for(b"")
    source.stream.read.side_effect = OSError("Microphone input overflow")
    with pytest.raises(OSError, match="overflow"):
        listen_for_speech(source, detector_for([]), timeout=1, limit=30, pause=0.5)


def test_vad_capture_skips_energy_calibration_and_repeated_cues():
    detector = detector_for([0.9] * 5 + [0.1] * 16)
    source = source_for(b"\x01\x00" * RATE)
    feedback = Mock()
    with (
        patch("iris_droid.client.open_microphone") as microphone,
        patch("speech_recognition.Recognizer") as recognizer,
    ):
        microphone.return_value.__enter__.return_value = source
        body = record_turn(
            1,
            30,
            10,
            None,
            pause_threshold=0.5,
            vad=detector,
            quiet=True,
            feedback=feedback,
        )
    recognizer.return_value.adjust_for_ambient_noise.assert_not_called()
    recognizer.return_value.listen.assert_not_called()
    feedback.listening.assert_not_called()
    feedback.recorded.assert_not_called()
    assert body.startswith(b"RIFF")


@pytest.mark.parametrize("companion", [False, True])
def test_rejected_noise_stays_quiet_and_only_speech_reaches_the_server(companion):
    events = []
    opened = False
    captures = 0
    detector = Mock()
    # Loud noise has a low speech probability; the quiet marker represents speech.
    detector.probability.side_effect = lambda pcm: 0.9 if pcm[:2] == b"\x01\x00" else 0.1

    @contextmanager
    def microphone(*args):
        nonlocal opened, captures
        captures += 1
        if companion and captures == 1:
            pcm = b"\x01\x00" * 512 * 5 + b"\xff\x7f" * RATE
        elif captures == (3 if companion else 2):
            pcm = b"\x01\x00" * 512 * 5 + b"\xff\x7f" * RATE
        else:
            pcm = b"\xff\x7f" * RATE * 2
        opened = True
        try:
            yield source_for(pcm)
        finally:
            opened = False

    def play(*args, **kwargs):
        assert not opened  # No cue or busy sound can leak into capture.
        events.append("sound")

    replies = (
        [TurnReply(heard="companion mode on", text="On", companion_mode=True)] if companion else []
    ) + [TurnReply(heard="hello", text="Bye", finished=True)]
    # Force a short companion interval so the all-noise capture raises an idle
    # timeout. Its next request must be an opener, rather than noise audio.
    if companion:
        replies[-1] = TurnReply(heard="", text="An opener", finished=True)
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "vad-regression-test-connection-key"}),
        patch("iris_droid.vad.SileroVAD", return_value=detector),
        patch("iris_droid.client.open_microphone", side_effect=microphone),
        patch("iris_droid.client.play_wav", side_effect=play),
        patch("iris_droid.client.stop_playback", side_effect=lambda: events.append("stop")),
        patch("iris_droid.client.DroidClient.turn", side_effect=replies) as turn,
        patch("iris_droid.client.CompanionTimer") as timer_type,
    ):
        timer = timer_type.return_value
        timer.enabled = False
        timer.remaining.return_value = 1
        timer.update.side_effect = lambda enabled, **kwargs: setattr(timer, "enabled", enabled)
        assert (
            iris.main(
                [
                    "client",
                    "--activation",
                    "continuous",
                    "--vad-model",
                    "model.onnx",
                    "--listen-timeout",
                    "1",
                    "--pause-threshold",
                    "0.5",
                ]
            )
            == 0
        )
    assert turn.call_count == (2 if companion else 1)
    if companion:
        assert turn.call_args.kwargs == {"text_only": False, "companion_idle": True}
        assert captures == 2
    else:
        assert captures == 2
    # One initial ready cue; one busy sound per accepted audio request. The
    # proactive request is silent, and noise adds neither cues nor requests.
    assert events == ["sound", "sound", "stop"]


def test_explicit_model_preparation_needs_neither_token_nor_audio(tmp_path):
    path = tmp_path / "model.onnx"
    with (
        patch("iris_droid.vad.download_model") as download,
        patch("iris_droid.client.open_microphone") as microphone,
        patch("iris_droid.vad.SileroVAD") as model,
    ):
        assert iris.main(["client", "--download-vad-model", str(path)]) == 0
    download.assert_called_once_with(path)
    microphone.assert_not_called()
    model.assert_not_called()


def test_model_download_verifies_content_before_overwriting(tmp_path):
    path = tmp_path / "vad.onnx"
    path.write_bytes(b"previous")
    with (
        patch(
            "iris_droid.vad.httpx.get",
            return_value=httpx.Response(
                200, content=b"bad model", request=httpx.Request("GET", "https://example.com")
            ),
        ),
        pytest.raises(ValueError, match="checksum"),
    ):
        download_model(path)
    assert path.read_bytes() == b"previous"


def test_missing_model_fails_before_audio_or_transport(tmp_path):
    with patch("iris_droid.client.open_microphone") as microphone, patch("httpx.Client") as client:
        assert iris.main(["client", "--vad-model", str(tmp_path / "missing.onnx")]) == 1
    microphone.assert_not_called()
    client.assert_not_called()


def test_text_mode_does_not_load_vad_or_open_audio():
    with (
        patch("iris_droid.vad.SileroVAD") as model,
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "vad-test-connection-key"}),
        patch("iris_droid.client.ConsoleInput.read", return_value="exit"),
        patch("iris_droid.client.DroidClient.turn", side_effect=KeyboardInterrupt()),
    ):
        assert iris.main(["client", "--text", "--vad-model", "missing.onnx"]) == 0
    model.assert_not_called()


@pytest.mark.parametrize("threshold", ["0", "1", "nan", "inf"])
def test_invalid_vad_threshold_fails_before_model_or_audio(threshold):
    with patch("iris_droid.vad.SileroVAD") as model:
        assert (
            iris.main(["client", "--vad-model", "missing.onnx", "--vad-threshold", threshold]) == 1
        )
    model.assert_not_called()
