"""Reply start padding and opt-in diagnostics, with no audio hardware."""

import base64
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

import iris
from iris_droid.client import play_wav
from iris_droid.protocol import TurnReply, write_wav

SAMPLES = np.arange(1, 2401, dtype="<i2")
WAV = write_wav(SAMPLES.tobytes(), 24000)


@pytest.mark.parametrize("seconds", [0, 0.3, 1.0])
def test_lead_in_is_silence_in_same_stream_and_keeps_every_speech_sample(seconds):
    with (
        patch("sounddevice.check_output_settings"),
        patch("sounddevice.play") as play,
        patch("sounddevice.get_status", return_value=None),
    ):
        play_wav(WAV, 7, lead_in=seconds)
    samples, rate = play.call_args.args
    padding = round(rate * seconds)
    assert rate == 24000 and len(samples) == len(SAMPLES) + padding
    np.testing.assert_array_equal(samples[:padding], np.zeros(padding, dtype="<i2"))
    np.testing.assert_array_equal(samples[padding:], SAMPLES)
    assert play.call_args.kwargs == {"device": 7, "blocking": True, "latency": "high"}


def test_optional_primer_and_speech_share_one_playback_stream():
    with (
        patch("sounddevice.check_output_settings"),
        patch("sounddevice.play") as play,
        patch("sounddevice.get_status", return_value=None),
    ):
        play_wav(WAV, 7, lead_in=0.3, primer=True)
    play.assert_called_once()
    samples, rate = play.call_args.args
    assert np.any(samples[:2160] != 0)  # Actual packaged 90 ms chirp.
    assert np.all(samples[2160 : 2160 + 7200] == 0)
    np.testing.assert_array_equal(samples[-len(SAMPLES) :], SAMPLES)


def test_playback_reports_driver_underrun(capsys):
    with (
        patch("sounddevice.check_output_settings"),
        patch("sounddevice.play"),
        patch("sounddevice.get_status", return_value=SimpleNamespace(output_underflow=True)),
    ):
        play_wav(WAV)
    assert "Speaker playback underrun" in capsys.readouterr().out


def test_local_playback_uses_reply_settings_without_network_or_microphone(tmp_path):
    path = tmp_path / "reply.wav"
    path.write_bytes(WAV)
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("httpx.Client") as transport,
        patch("iris_droid.client.record_turn") as record,
        patch("iris_droid.client.play_wav") as play,
    ):
        assert (
            iris.main(
                [
                    "client",
                    "--play-wav",
                    str(path),
                    "--speaker",
                    "7",
                    "--speech-lead-in",
                    "0.5",
                    "--speech-primer",
                ]
            )
            == 0
        )
    play.assert_called_once_with(WAV, 7, lead_in=0.5, primer=True)
    record.assert_not_called()
    transport.assert_not_called()


def test_opt_in_saved_reply_is_unmodified_before_playback(tmp_path):
    path = tmp_path / "private" / "reply.wav"
    path.parent.mkdir()
    path.write_bytes(b"previous reply")

    def play(data, device, **kwargs):
        assert path.read_bytes() == WAV
        assert data == WAV and kwargs == {"lead_in": 0.3, "primer": False}

    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("iris_droid.client.record_turn", return_value=WAV),
        patch("iris_droid.client.play_wav", side_effect=play),
        patch(
            "iris_droid.client.DroidClient.turn",
            return_value=TurnReply(
                heard="exit",
                text="goodbye",
                audio=base64.b64encode(WAV).decode(),
                finished=True,
            ),
        ),
    ):
        assert (
            iris.main(
                [
                    "client",
                    "--activation",
                    "continuous",
                    "--no-sounds",
                    "--save-reply",
                    str(path),
                ]
            )
            == 0
        )
    assert path.read_bytes() == WAV


def test_default_does_not_save_reply_audio():
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("iris_droid.client.record_turn", return_value=WAV),
        patch("iris_droid.client.play_wav"),
        patch("pathlib.Path.write_bytes") as save,
        patch(
            "iris_droid.client.DroidClient.turn",
            return_value=TurnReply(
                heard="exit",
                text="goodbye",
                audio=base64.b64encode(WAV).decode(),
                finished=True,
            ),
        ),
    ):
        assert iris.main(["client", "--activation", "continuous", "--no-sounds"]) == 0
    save.assert_not_called()


@pytest.mark.parametrize("seconds", [-1, 2.1, float("nan"), float("inf")])
def test_invalid_lead_in_does_not_open_transport_or_speaker(seconds):
    with patch("httpx.Client") as transport, patch("sounddevice.play") as play:
        assert iris.main(["client", "--speech-lead-in", str(seconds)]) == 1
        with pytest.raises(ValueError, match="Speech lead-in"):
            play_wav(WAV, lead_in=seconds)
    transport.assert_not_called()
    play.assert_not_called()
