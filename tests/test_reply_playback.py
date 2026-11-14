"""Reply start padding and opt-in diagnostics, with no audio hardware."""

import base64
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import numpy as np
import pytest

import iris
from iris_droid.client import play_stream, play_wav, report_voice_timing
from iris_droid.protocol import VOICE_STREAM_TYPE, TurnReply, VoiceTiming, write_wav

SAMPLES = np.arange(1, 2401, dtype="<i2")
WAV = write_wav(SAMPLES.tobytes(), 24000)


def test_stream_plays_before_next_chunk_and_keeps_one_output_device():
    from contextlib import contextmanager

    import sounddevice as sd

    first = np.ones(3840, dtype="<i2")
    second = np.full(240, 2, dtype="<i2")
    clock = SimpleNamespace(currentTime=10.0, outputBufferDacTime=10.10)
    status = SimpleNamespace(output_underflow=False)
    played = []

    @contextmanager
    def output(**kwargs):
        block = np.empty((len(first), 1), dtype="<i2")
        kwargs["callback"](block, len(block), clock, status)
        played.append(block[:, 0].copy())
        yield

    # Simulate the single stream consuming its remaining buffer before wait().
    def finished():
        options = stream.call_args.kwargs
        block = np.empty((480, 1), dtype="<i2")
        with pytest.raises(sd.CallbackStop):
            options["callback"](block, len(block), clock, status)
        played.append(block[:, 0].copy())
        return True

    def chunks():
        yield first.tobytes()
        assert len(played) == 1
        yield second.tobytes()

    with (
        patch("sounddevice.check_output_settings"),
        patch("sounddevice.OutputStream", side_effect=output) as stream,
        patch("iris_droid.client.Event") as event,
        patch("iris_droid.client.time_ns", return_value=2_000_000_000),
    ):
        event.return_value.wait.side_effect = finished
        assert play_stream(chunks(), 24000, 7, lead_in=0, primer=False) == 2_100_000_000
    stream.assert_called_once()
    np.testing.assert_array_equal(played[0], first)
    np.testing.assert_array_equal(played[1][:240], second)
    assert not played[1][240:].any()


def test_voice_trace_uses_dac_time_after_padding_and_wav_silence(capsys):
    import sounddevice as sd

    chunks = []

    def stream(**kwargs):
        for current in (10.0, 10.01, 10.02):
            block = np.empty((240, 1), dtype="<i2")
            chunks.append(block)
            with_audio_time = SimpleNamespace(
                currentTime=current, outputBufferDacTime=current + 0.1
            )
            try:
                kwargs["callback"](
                    block, len(block), with_audio_time, SimpleNamespace(output_underflow=False)
                )
            except sd.CallbackStop:
                kwargs["finished_callback"]()
                break
        from contextlib import nullcontext

        return nullcontext()

    timing = VoiceTiming(
        engine="chatterbox",
        words=18,
        started_ns=1_000_000_000,
        synthesis_ns=800_000_000,
        audio_seconds=0.011,
    )
    wav = write_wav(b"\0\0" * 24 + b"\1\0" * 240, 24000)
    with (
        patch("sounddevice.check_output_settings"),
        patch("sounddevice.OutputStream", side_effect=stream),
        patch("iris_droid.client.time_ns", return_value=2_000_000_000),
    ):
        play_wav(wav, lead_in=0.01, on_speech_start=lambda ns: report_voice_timing(timing, ns))
    assert chunks[0].sum() == 0
    assert chunks[1][24, 0] == 1
    assert (
        "18 words) | synthesis 0.800 s | audio length 0.011 s | "
        "synthesis start to speaker 1.101 s (estimated) | after synthesis 0.301 s"
    ) in capsys.readouterr().out


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


def test_no_speech_stream_plays_complete_server_wav_without_saving():
    def respond(request):
        assert request.url.path == "/v1/voice"
        assert request.headers.get("Accept") != VOICE_STREAM_TYPE
        reply = TurnReply(
            heard="exit", text="goodbye", audio=base64.b64encode(WAV).decode(), finished=True
        )
        return httpx.Response(200, json=reply.model_dump())

    transport = httpx.Client(transport=httpx.MockTransport(respond))
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("iris_droid.client.httpx.Client", return_value=transport),
        patch("iris_droid.client.record_turn", return_value=WAV),
        patch("iris_droid.client.play_wav") as play,
        patch("iris_droid.client.play_stream") as stream,
        patch("pathlib.Path.write_bytes") as save,
    ):
        assert (
            iris.main(["client", "--activation", "continuous", "--no-sounds", "--no-speech-stream"])
            == 0
        )
    play.assert_called_once_with(WAV, None, lead_in=0.3, primer=False)
    stream.assert_not_called()
    save.assert_not_called()


@pytest.mark.parametrize("seconds", [-1, 2.1, float("nan"), float("inf")])
def test_invalid_lead_in_does_not_open_transport_or_speaker(seconds):
    with patch("httpx.Client") as transport, patch("sounddevice.play") as play:
        assert iris.main(["client", "--speech-lead-in", str(seconds)]) == 1
        with pytest.raises(ValueError, match="Speech lead-in"):
            play_wav(WAV, lead_in=seconds)
    transport.assert_not_called()
    play.assert_not_called()
