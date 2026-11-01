import base64
import io
import json
import sys
import threading
import wave
from http.server import HTTPServer
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
import numpy as np
import pytest
import speech_recognition as sr

import iris
from iris_droid.client import DroidClient, open_microphone, play_wav, record_turn
from iris_droid.protocol import MAX_BODY, read_wav, validate_token, write_wav
from iris_droid.server import Brain, handler_for
from iris_droid.wake import WakeDetector
from iris_settings import Settings

TOKEN = "test"
RECORDING = write_wav(b"\x01\x00" * 1600, 16000)


def test_recording_loads_the_real_packaged_cue_before_reopening_microphone() -> None:
    events = []
    microphone = Mock()
    microphone.__enter__ = Mock(side_effect=lambda: events.append("opened") or Mock())
    microphone.__exit__ = Mock(side_effect=lambda *args: events.append("closed"))
    recognizer = Mock()
    recognizer.listen.return_value = sr.AudioData(b"\x01\x00" * 1600, 16000, 2)

    def play(samples, rate, *, device, blocking):
        assert rate == 24000 and len(samples) > 0
        assert device == 7 and blocking is True
        events.append("cue")

    with (
        patch("speech_recognition.Microphone", return_value=microphone),
        patch("speech_recognition.Recognizer", return_value=recognizer),
        patch("sounddevice.play", side_effect=play),
        patch("sounddevice.check_output_settings"),
    ):
        recording = record_turn(None, 30, 10, None, speaker=7)
    assert events == ["opened", "closed", "cue", "opened", "closed"]
    assert read_wav(recording, input_audio=True) == (b"\x01\x00" * 1600, 16000)


def make_brain() -> tuple[Brain, httpx.Client]:
    def chat(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "Hello, Luke"}}]},
        )

    client = httpx.Client(transport=httpx.MockTransport(chat))
    brain = Brain(
        Settings(),
        client,
        Mock(recognize=Mock(return_value="Hello Iris")),
        Mock(synthesize=Mock(return_value=(np.ones(100, dtype=np.float32) * 0.1, 24000))),
    )
    return brain, client


def test_voice_roundtrip_and_session_isolation_without_audio_devices() -> None:
    brain, transport = make_brain()
    with transport, patch("sounddevice.play") as play:
        reply = brain.turn("laptop", RECORDING)
        pcm, rate = read_wav(base64.b64decode(reply.audio))
        assert rate == 24000 and len(pcm) == 200
        assert reply.heard == "Hello Iris" and reply.text == "Hello, Luke"
        assert len(brain.sessions["laptop"].history) == 2
        brain.turn("pi", b"another conversation", text_only=True)
        assert len(brain.sessions["pi"].history) == 2
        brain.turn("laptop", b"follow up", text_only=True)
        assert len(brain.sessions["laptop"].history) == 4
        play.assert_not_called()


def test_failed_synthesis_rolls_back_history() -> None:
    brain, transport = make_brain()
    with transport:
        brain.turn("pi", b"first", text_only=True)
        previous = brain.sessions["pi"].history.copy()
        brain.tts.synthesize.side_effect = RuntimeError("no audio")
        with pytest.raises(RuntimeError):
            brain.turn("pi", RECORDING)
        assert brain.sessions["pi"].history == previous


def test_exit_clears_session_and_silence_never_reaches_chat() -> None:
    brain, transport = make_brain()
    with transport:
        brain.turn("pi", b"hello", text_only=True)
        reply = brain.turn("pi", b"exit", text_only=True)
        assert reply.finished and "pi" not in brain.sessions
        brain.stt.recognize.return_value = " "
        with pytest.raises(sr.UnknownValueError):
            brain.turn("pi", RECORDING)
        assert "pi" not in brain.sessions


@pytest.mark.parametrize("mode", ["stereo", "rate", "truncated", "too-long", "garbage"])
def test_bad_recordings_are_rejected_before_recognition(mode: str) -> None:
    brain, transport = make_brain()
    with transport:
        data = RECORDING
        if mode == "stereo":
            output = io.BytesIO()
            with wave.open(output, "wb") as target:
                target.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
                target.writeframes(b"\x00" * 400)
            data = output.getvalue()
        elif mode == "rate":
            data = write_wav(b"\x00\x00" * 100, 24000)
        elif mode == "truncated":
            data = data[:-5]
        elif mode == "too-long":
            brain.settings.phrase_time_limit = 0.01
        else:
            data = b"not a wav"
        with pytest.raises(ValueError):
            brain.turn("pi", data)
        brain.stt.recognize.assert_not_called()


def test_session_memory_is_bounded() -> None:
    brain, transport = make_brain()
    with transport:
        for index in range(17):
            brain.turn(str(index), b"hello", text_only=True)
    assert len(brain.sessions) == 16 and "0" not in brain.sessions


def test_real_http_transport_auth_validation_and_voice_turn() -> None:
    brain, transport = make_brain()
    with transport, HTTPServer(("127.0.0.1", 0), handler_for(brain, TOKEN)) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with httpx.Client(trust_env=False, timeout=5) as http:
                url = f"http://127.0.0.1:{server.server_port}"
                assert http.post(f"{url}/v1/voice", content=b"bad").status_code == 401
                headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "audio/wav"}
                assert (
                    http.post(f"{url}/v1/voice", headers=headers, content=RECORDING).status_code
                    == 400
                )
                headers["X-Iris-Session"] = str(uuid4())
                assert (
                    http.post(f"{url}/v1/voice", headers=headers, content=b"bad").status_code == 400
                )
                droid = DroidClient(url, TOKEN, http)
                result = droid.turn(RECORDING)
                assert result.audio and result.text == "Hello, Luke"
                assert droid.turn(b"exit", text_only=True).finished
                with (
                    patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
                    patch("builtins.input", side_effect=["hello", "exit"]),
                    patch("iris_droid.client.record_turn") as record,
                    patch("iris_droid.client.play_wav") as play,
                ):
                    assert iris.main(["client", "--server", url, "--text"]) == 0
                record.assert_not_called()
                play.assert_not_called()
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_client_rejects_redirects_and_oversized_replies() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (MAX_BODY + 1))

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ValueError, match="size limit"):
            DroidClient("http://pc:8765", TOKEN, http).turn(RECORDING)

    requests = []

    def redirect(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(307, headers={"Location": "http://elsewhere/v1/voice"})

    with httpx.Client(transport=httpx.MockTransport(redirect)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            DroidClient("http://pc:8765", TOKEN, http).turn(RECORDING)
    assert len(requests) == 1


def test_wake_uses_local_path_and_only_accepts_complete_phrase(tmp_path) -> None:
    detector = Mock()
    detector.AcceptWaveform.side_effect = [True, False, True]
    detector.Result.side_effect = ['{"text":"[unk]"}', '{"text":"hey iris"}']
    vosk = SimpleNamespace(Model=Mock(), KaldiRecognizer=Mock(return_value=detector))
    source = SimpleNamespace(
        stream=Mock(read=Mock(return_value=b"pcm")), CHUNK=1024, SAMPLE_RATE=44100
    )
    with patch.dict(sys.modules, {"vosk": vosk}):
        wake = WakeDetector(tmp_path, "Hey Iris")
        wake.wait(source)
    vosk.Model.assert_called_once_with(str(tmp_path))
    assert source.stream.read.call_count == 3
    assert vosk.KaldiRecognizer.call_args.args[1] == 44100
    assert json.loads(vosk.KaldiRecognizer.call_args.args[2]) == ["hey iris", "[unk]"]


def test_short_droid_key_warns_but_is_accepted() -> None:
    with pytest.warns(UserWarning, match="Your chosen key will still work"):
        validate_token("x")


@pytest.mark.parametrize("token", ["", "   ", "key\r\nheader", "key\x00", "non-ascii-£"])
def test_empty_or_invalid_header_keys_are_rejected(token: str) -> None:
    with pytest.raises(ValueError, match="non-empty ASCII"):
        validate_token(token)


@pytest.mark.parametrize("native_rate", [44100, 48000])
def test_native_capture_is_converted_to_server_format(native_rate):
    microphone = Mock()
    source = Mock(SAMPLE_RATE=native_rate)
    microphone.__enter__ = Mock(return_value=source)
    microphone.__exit__ = Mock()
    # A second of real PCM conversion must remain a second at the server rate.
    captured = sr.AudioData(b"\x01\x00" * native_rate, native_rate, 2)
    recognizer = Mock()
    recognizer.listen.return_value = captured
    wake = Mock()
    with (
        patch("speech_recognition.Microphone", return_value=microphone) as factory,
        patch("speech_recognition.Recognizer", return_value=recognizer),
        patch("iris_droid.client.play_wav"),
    ):
        pcm, rate = read_wav(record_turn(1, 30, 10, wake, speaker=1), input_audio=True)
    assert rate == 16000 and abs(len(pcm) // 2 - 16000) <= 1
    assert all(call.kwargs == {"device_index": 1} for call in factory.call_args_list)
    assert factory.call_count == 2
    wake.wait.assert_called_once_with(source)
    assert microphone.__exit__.call_count == 2


@pytest.mark.parametrize("output_rate", [44100, 48000])
def test_playback_resamples_when_hardware_rejects_voice_rate(output_rate):
    import sounddevice as sd

    # A known tone checks duration and pitch, rather than just the rate argument.
    samples = (np.sin(2 * np.pi * 440 * np.arange(24000) / 24000) * 12000).astype("<i2")
    with (
        patch("sounddevice.check_output_settings", side_effect=[sd.PortAudioError("rate"), None]),
        patch(
            "sounddevice.query_devices", return_value={"default_samplerate": output_rate}
        ) as query,
        patch("sounddevice.play") as play,
    ):
        play_wav(write_wav(samples.tobytes(), 24000), device=1)
    actual, rate = play.call_args.args
    assert rate == output_rate and abs(len(actual) - output_rate) <= 2
    frequency = np.fft.rfftfreq(len(actual), 1 / rate)[np.argmax(abs(np.fft.rfft(actual)))]
    assert abs(frequency - 440) < 2
    assert play.call_args.kwargs == {"device": 1, "blocking": True}
    query.assert_called_once_with(1, "output")


def test_supported_playback_preserves_samples_without_querying_default():
    with (
        patch("sounddevice.check_output_settings"),
        patch("sounddevice.query_devices") as query,
        patch("sounddevice.play") as play,
    ):
        play_wav(RECORDING, device=1)
    query.assert_not_called()
    samples, rate = play.call_args.args
    assert rate == 16000 and samples.tobytes() == read_wav(RECORDING)[0]


def test_failed_output_preflight_reports_device_and_never_plays():
    import sounddevice as sd

    with (
        patch("sounddevice.check_output_settings", side_effect=sd.PortAudioError("unsupported")),
        patch("sounddevice.query_devices", return_value={"default_samplerate": 44100}),
        patch("sounddevice.play") as play,
        pytest.raises(OSError, match="Speaker 1.*unsupported"),
    ):
        play_wav(RECORDING, device=1)
    play.assert_not_called()


def test_failed_microphone_open_does_not_mask_error_with_cleanup():
    microphone = Mock()
    microphone.__enter__ = Mock(return_value=SimpleNamespace(stream=None))
    microphone.__exit__ = Mock(side_effect=AttributeError("None has no close"))
    with (
        patch("speech_recognition.Microphone", return_value=microphone),
        pytest.raises(OSError, match="Cannot open microphone 1: no capture stream"),
        open_microphone(1),
    ):
        pytest.fail("A failed capture stream must never be used")
    microphone.__exit__.assert_not_called()


def test_microphone_is_closed_when_recording_raises():
    microphone = Mock()
    microphone.__enter__ = Mock(return_value=Mock())
    microphone.__exit__ = Mock()
    with patch("speech_recognition.Microphone", return_value=microphone):
        with pytest.raises(sr.WaitTimeoutError), open_microphone(1):
            raise sr.WaitTimeoutError()
    microphone.__exit__.assert_called_once()
