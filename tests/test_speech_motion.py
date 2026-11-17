"""Speech energy follows output time, including pauses and interrupted playback."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from iris_domain.expression import FACE_EXPRESSIONS, read_model_reply
from iris_droid.client import play_stream, play_wav
from iris_droid.face_console import DisplayState, frame_for
from iris_droid.face_display import FaceDisplay
from iris_droid.protocol import write_wav
from iris_droid.speech_motion import SpeechEnvelope, read_mouth, sample_mouth


def test_envelope_waits_for_dac_and_closes_during_silence(monkeypatch):
    monkeypatch.setattr("iris_droid.speech_motion.monotonic", lambda: 10)
    envelope = SpeechEnvelope()
    samples = np.concatenate([np.zeros(480), np.full(480, 12000), np.zeros(480)]).astype("int16")
    envelope.feed(samples, 24000, 0.1)
    points = read_mouth(envelope.snapshot(10), 10)
    assert sample_mouth(points, 10.09) == 0
    assert sample_mouth(points, 10.11) == 0
    assert sample_mouth(points, 10.13) == 1
    assert sample_mouth(points, 10.15) == 0
    assert sample_mouth(points, 12) == 0
    envelope.clear()
    assert envelope.snapshot(10) == []


def test_envelope_is_bounded_and_never_keeps_pcm(monkeypatch):
    now = 0
    monkeypatch.setattr("iris_droid.speech_motion.monotonic", lambda: now)
    envelope = SpeechEnvelope()
    for _ in range(200):
        envelope.feed(np.full(480, 1000, dtype="int16"), 24000, 0.1)
        now += 0.02
    assert len(envelope.snapshot(0)) <= 128
    assert len(envelope.snapshot(now + 1)) == 1
    for bad in ([[-1, float("nan")]], [[0, 2]], [[0, 1], [-1, 0]], [[0]], "invalid"):
        assert read_mouth(bad, 0) == ()


def test_blink_does_not_interrupt_audible_syllable():
    speaking = DisplayState("rivet", "speaking", "amused", "black", mouth=((0, 1), (0.12, 0)))
    assert frame_for(speaking, 0.095) == ("amused", "blink|jaw-wide")
    assert frame_for(speaking, 0.48) == ("amused", "rest")
    quiet = DisplayState("rivet", "speaking", "amused", "black", mouth=())
    assert frame_for(quiet, 0.48) == ("amused", "rest")


@pytest.mark.parametrize("expression", FACE_EXPRESSIONS)
def test_all_emotions_survive_cue_parsing_without_entering_spoken_text(expression):
    reply = read_model_reply(f"[[expression:{expression}]]\nHello.", face=True)
    assert reply.expression == expression and reply.text == "Hello."


def test_speaking_lifecycle_clears_levels_after_playback_failure(monkeypatch):
    monkeypatch.setattr("iris_droid.speech_motion.monotonic", lambda: 10)
    monkeypatch.setattr("iris_droid.face_display.monotonic", lambda: 10)
    display = FaceDisplay("signal")
    with pytest.raises(RuntimeError), display.speaking("delighted"):
        display.speech.feed(np.full(480, 16000, dtype="int16"), 24000, 0.1)
        assert json.loads(display.state())["mouth"][0] == [0.1, 1]
        raise RuntimeError("speaker disconnected")
    assert display.speech.snapshot(10) == []
    assert "mouth" not in json.loads(display.state())


@pytest.mark.parametrize("streamed", [False, True])
def test_pcm_callbacks_keep_primer_quiet_and_preserve_output(streamed):
    import sounddevice as sd

    samples = np.full(4800, 12000, dtype="int16")
    output = []
    animation = []
    options = {}

    def stream(**kwargs):
        options.update(kwargs)
        return nullcontext()

    def drain(timeout):
        for _ in range(100):
            block = np.empty((480, 1), dtype="int16")
            try:
                options["callback"](
                    block,
                    480,
                    SimpleNamespace(currentTime=10, outputBufferDacTime=10.1),
                    SimpleNamespace(output_underflow=False),
                )
            except sd.CallbackStop:
                output.append(block.copy())
                return True
            output.append(block.copy())
        raise AssertionError("Playback never drained")

    with (
        patch("sounddevice.check_output_settings"),
        patch("sounddevice.OutputStream", side_effect=stream),
        patch("iris_droid.client.Event") as event,
    ):
        event.return_value.wait.side_effect = drain

        def on_audio(pcm, rate, delay):
            animation.append(pcm.copy())
            assert rate == 24000 and delay == pytest.approx(0.1)

        if streamed:
            play_stream(
                iter([samples.tobytes()]), 24000, None, lead_in=0.02, primer=True, on_audio=on_audio
            )
        else:
            play_wav(
                write_wav(samples.tobytes(), 24000), lead_in=0.02, primer=True, on_audio=on_audio
            )
    visual = np.concatenate(animation)
    audible = np.concatenate(output)[:, 0]
    # The packaged primer is 90ms, then 20ms silence. Its real PCM stays audible.
    prefix = 2160 + 480
    assert audible[:2160].any()
    assert not visual[:prefix].any()
    np.testing.assert_array_equal(visual[prefix : prefix + len(samples)], samples)
    np.testing.assert_array_equal(audible[prefix : prefix + len(samples)], samples)
    assert not visual[prefix + len(samples) :].any()
