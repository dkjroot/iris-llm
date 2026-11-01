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
    microphone.__enter__ = Mock(
        side_effect=lambda: events.append("opened") or Mock(CHUNK=8192, SAMPLE_RATE=44100)
    )
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
                    assert iris.main(["client", "--no-sounds", "--server", url, "--text"]) == 0
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


@pytest.mark.parametrize("native_rate", [8000, 16000, 22050, 44100, 48000, 96000])
def test_native_capture_is_converted_to_server_format(native_rate):
    microphone = Mock()
    source = Mock(SAMPLE_RATE=native_rate, CHUNK=8192)
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
    assert all(
        call.kwargs == {"device_index": 1, "chunk_size": 8192} for call in factory.call_args_list
    )
    assert factory.call_count == 3
    wake.wait.assert_called_once_with(source)
    assert microphone.__exit__.call_count == 3


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


@pytest.mark.parametrize("buffer_frames", [8192, 16384])
def test_capture_buffer_and_checked_reads_reach_the_native_stream(buffer_frames):
    native = Mock()
    native.read.return_value = b"\x01\x00" * buffer_frames
    original = SimpleNamespace(pyaudio_stream=native, close=Mock())
    source = SimpleNamespace(stream=original)
    microphone = Mock()
    microphone.__enter__ = Mock(return_value=source)
    microphone.__exit__ = Mock(side_effect=lambda *args: source.stream.close())
    with patch("speech_recognition.Microphone", return_value=microphone) as factory:
        with open_microphone(1, buffer_frames) as opened:
            assert opened.stream.read(buffer_frames) == b"\x01\x00" * buffer_frames
    factory.assert_called_once_with(device_index=1, chunk_size=buffer_frames)
    native.read.assert_called_once_with(buffer_frames, exception_on_overflow=True)
    original.close.assert_called_once()


def test_capture_overflow_aborts_before_any_network_turn(capsys):
    native = Mock()
    native.read.side_effect = OSError(-9981, "Input overflowed")
    source = SimpleNamespace(
        stream=SimpleNamespace(pyaudio_stream=native, close=Mock()),
        SAMPLE_RATE=44100,
        CHUNK=8192,
    )
    microphone = Mock()
    microphone.__enter__ = Mock(return_value=source)
    microphone.__exit__ = Mock(side_effect=lambda *args: source.stream.close())
    recognizer = Mock()
    recognizer.adjust_for_ambient_noise.side_effect = lambda s, **kw: s.stream.read(8192)
    with (
        patch("speech_recognition.Microphone", return_value=microphone),
        patch("speech_recognition.Recognizer", return_value=recognizer),
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch("iris_droid.client.DroidClient.turn") as turn,
        patch("iris_droid.client.play_wav") as play,
    ):
        assert iris.main(["client", "--no-sounds", "--activation", "continuous"]) == 1
    output = capsys.readouterr().out
    assert "input overflow" in output and "recording discarded" in output
    assert "--microphone-buffer" in output
    turn.assert_not_called()
    play.assert_not_called()
    microphone.__exit__.assert_called_once()


def test_capture_rejects_short_reads_and_preserves_other_device_errors():
    from iris_droid.capture import CheckedMicrophoneStream

    native = Mock()
    stream = CheckedMicrophoneStream(SimpleNamespace(pyaudio_stream=native, close=Mock()))
    native.read.return_value = b"\x00\x00"
    with pytest.raises(OSError, match="incomplete audio"):
        stream.read(8192)
    native.read.side_effect = OSError(-9999, "Device disconnected")
    with pytest.raises(OSError, match="Device disconnected") as failure:
        stream.read(8192)
    assert failure.value.errno == -9999


def test_client_passes_requested_buffer_to_recording():
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch("iris_droid.client.record_turn", side_effect=KeyboardInterrupt) as record,
    ):
        assert (
            iris.main(
                [
                    "client",
                    "--no-sounds",
                    "--activation",
                    "continuous",
                    "--microphone-buffer",
                    "16384",
                ]
            )
            == 0
        )
    assert record.call_args.args[5] == 16384


@pytest.mark.parametrize("frames", [0, 255, 65537])
def test_invalid_capture_buffer_does_not_open_devices_or_network(frames):
    with (
        patch("speech_recognition.Microphone") as microphone,
        patch("httpx.Client") as transport,
    ):
        assert iris.main(["client", "--no-sounds", "--microphone-buffer", str(frames)]) == 1
        with pytest.raises(ValueError, match="Microphone buffer"):
            with open_microphone(1, frames):
                pytest.fail("Invalid buffer must not open")
    microphone.assert_not_called()
    transport.assert_not_called()


@pytest.mark.parametrize("activation", ["enter", "continuous", "wake"])
@pytest.mark.parametrize("echo", [False, True])
def test_echo_plays_exact_outgoing_recording_before_send(activation, echo, capsys):
    events = []

    def record(*args, **kwargs):
        events.append("recorded")
        return RECORDING

    def play(data, device):
        assert data is RECORDING and device == 7
        events.append("echoed")

    def turn(body, *, text_only):
        assert body is RECORDING and text_only is False
        events.append("sent")
        return SimpleNamespace(heard="exit", text="Goodbye", audio="", finished=True)

    argv = ["client", "--no-sounds", "--activation", activation, "--speaker", "7"]
    if echo:
        argv.append("--echo-recording")
    if activation == "wake":
        argv.extend(["--wake-model", "."])
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch("builtins.input", return_value=""),
        patch("iris_droid.client.WakeDetector"),
        patch("iris_droid.client.record_turn", side_effect=record),
        patch("iris_droid.client.play_wav", side_effect=play),
        patch("iris_droid.client.DroidClient.turn", side_effect=turn),
    ):
        assert iris.main(argv) == 0
    assert events == (["recorded", "echoed", "sent"] if echo else ["recorded", "sent"])
    output = capsys.readouterr().out
    assert ("Recorded 0.1 seconds" in output) is echo


@pytest.mark.parametrize(
    "failure, code", [(OSError("Speaker unavailable"), 1), (KeyboardInterrupt(), 0)]
)
def test_failed_or_interrupted_echo_never_sends_the_recording(failure, code):
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch("iris_droid.client.record_turn", return_value=RECORDING),
        patch("iris_droid.client.play_wav", side_effect=failure),
        patch("iris_droid.client.DroidClient.turn") as turn,
    ):
        assert (
            iris.main(["client", "--no-sounds", "--activation", "continuous", "--echo-recording"])
            == code
        )
    turn.assert_not_called()


def test_text_mode_ignores_recording_echo():
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch("builtins.input", return_value="exit"),
        patch("iris_droid.client.record_turn") as record,
        patch("iris_droid.client.play_wav") as play,
        patch(
            "iris_droid.client.DroidClient.turn",
            return_value=SimpleNamespace(heard="exit", text="Goodbye", audio="", finished=True),
        ) as turn,
    ):
        assert iris.main(["client", "--no-sounds", "--text", "--echo-recording"]) == 0
    turn.assert_called_once_with(b"exit", text_only=True)
    record.assert_not_called()
    play.assert_not_called()


def test_silence_does_not_echo_or_send():
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch(
            "iris_droid.client.record_turn",
            side_effect=[sr.WaitTimeoutError(), KeyboardInterrupt()],
        ),
        patch("iris_droid.client.play_wav") as play,
        patch("iris_droid.client.DroidClient.turn") as turn,
    ):
        assert (
            iris.main(["client", "--no-sounds", "--activation", "continuous", "--echo-recording"])
            == 0
        )
    play.assert_not_called()
    turn.assert_not_called()


class SyntheticMicrophone(sr.AudioSource):
    """Feed native-rate PCM through the real recognizer without audio hardware."""

    def __init__(self, pcm, rate=44100, chunk=8192):
        self.SAMPLE_RATE = rate
        self.SAMPLE_WIDTH = 2
        self.CHUNK = chunk
        self._data = io.BytesIO(pcm)
        self.stream = self

    def read(self, frames):
        return self._data.read(frames * 2)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._data.close()


def synthetic_audio(sections, rate=44100):
    return b"".join(
        np.full(round(seconds * rate), amplitude, dtype="<i2").tobytes()
        for seconds, amplitude in sections
    )


@pytest.mark.parametrize("chunk", [8192, 16384])
def test_sustained_speech_does_not_raise_its_own_silence_threshold(chunk):
    ambient = SyntheticMicrophone(synthetic_audio([(1, 0)]), chunk=chunk)
    speech = SyntheticMicrophone(synthetic_audio([(8, 4000), (3, 0)]), chunk=chunk)
    with (
        patch("iris_droid.client.open_microphone", side_effect=[ambient, speech]),
        patch("iris_droid.client.play_wav"),
    ):
        pcm, rate = read_wav(record_turn(1, 30, 10, None, buffer_frames=chunk))
    # Default dynamic adaptation cuts this steady signal off after about 2 s.
    samples = np.frombuffer(pcm, dtype="<i2")
    assert rate == 16000
    assert 7.9 <= np.count_nonzero(samples) / rate <= 8.1
    assert len(samples) / rate < 10  # Ends on silence, not the phrase cap or EOF.


def test_sentence_survives_short_pause_and_softer_ending():
    ambient = SyntheticMicrophone(synthetic_audio([(1, 0)]))
    speech = SyntheticMicrophone(synthetic_audio([(1, 4000), (0.9, 0), (2, 1000), (3, 0)]))
    with (
        patch("iris_droid.client.open_microphone", side_effect=[ambient, speech]),
        patch("iris_droid.client.play_wav"),
    ):
        pcm, rate = read_wav(record_turn(1, 30, 10, None))
    samples = np.frombuffer(pcm, dtype="<i2")
    assert np.count_nonzero(samples == 1000) / rate > 1.9
    assert len(samples) / rate >= 3.9


@pytest.mark.parametrize("pause, expected_second_phrase", [(1.2, False), (2.5, True)])
def test_configured_pause_controls_real_phrase_endpoint(pause, expected_second_phrase):
    ambient = SyntheticMicrophone(synthetic_audio([(1, 0)]))
    speech = SyntheticMicrophone(synthetic_audio([(1, 4000), (2, 0), (1, 1000), (4, 0)]))
    with (
        patch("iris_droid.client.open_microphone", side_effect=[ambient, speech]),
        patch("iris_droid.client.play_wav"),
    ):
        pcm, _ = read_wav(record_turn(1, 30, 10, None, pause_threshold=pause))
    assert bool(np.any(np.frombuffer(pcm, dtype="<i2") == 1000)) is expected_second_phrase


def test_client_passes_pause_setting_to_capture():
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch("iris_droid.client.record_turn", side_effect=KeyboardInterrupt) as record,
    ):
        assert (
            iris.main(
                ["client", "--no-sounds", "--activation", "continuous", "--pause-threshold", "2"]
            )
            == 0
        )
    assert record.call_args.args[6] == 2


@pytest.mark.parametrize("pause", [0, 0.49, 10.1, float("nan"), float("inf")])
def test_invalid_pause_setting_does_not_open_audio_or_network(pause):
    with (
        patch("iris_droid.client.open_microphone") as microphone,
        patch("httpx.Client") as transport,
    ):
        assert iris.main(["client", "--no-sounds", "--pause-threshold", str(pause)]) == 1
        with pytest.raises(ValueError, match="Pause threshold"):
            record_turn(1, 30, 10, None, pause_threshold=pause)
    microphone.assert_not_called()
    transport.assert_not_called()


@pytest.mark.parametrize("rate", [16000, 22050, 44100, 48000, 96000])
def test_explicit_capture_rate_is_used_for_both_opens_and_wake(rate):
    source = Mock(SAMPLE_RATE=rate, CHUNK=8192)
    microphone = Mock()
    microphone.__enter__ = Mock(return_value=source)
    microphone.__exit__ = Mock()
    recognizer = Mock()
    recognizer.listen.return_value = sr.AudioData(b"\x01\x00" * rate, rate, 2)
    wake = Mock()
    with (
        patch("speech_recognition.Microphone", return_value=microphone) as factory,
        patch("speech_recognition.Recognizer", return_value=recognizer),
        patch("iris_droid.client.play_wav"),
    ):
        pcm, output_rate = read_wav(record_turn(1, 30, 10, wake, sample_rate=rate))
    assert factory.call_count == 3
    assert all(
        call.kwargs == {"device_index": 1, "chunk_size": 8192, "sample_rate": rate}
        for call in factory.call_args_list
    )
    assert output_rate == 16000 and abs(len(pcm) // 2 - 16000) <= 2
    wake.wait.assert_called_once_with(source)
    assert source.SAMPLE_RATE == rate


@pytest.mark.parametrize("rate,chunk", [(8000, 8192), (16000, 16384), (48000, 8192)])
def test_calibration_reads_audio_even_when_buffer_exceeds_requested_duration(rate, chunk):
    ambient = SyntheticMicrophone(synthetic_audio([(3, 0)], rate), rate=rate, chunk=chunk)
    speech = SyntheticMicrophone(synthetic_audio([(2, 4000), (4, 0)], rate), rate=rate, chunk=chunk)
    recognizer = sr.Recognizer()
    with (
        patch("iris_droid.client.open_microphone", side_effect=[ambient, speech]),
        patch("iris_droid.client.play_wav"),
        patch("speech_recognition.Recognizer", return_value=recognizer),
    ):
        pcm, _ = read_wav(record_turn(1, 30, 10, None, calibration_duration=0.1))
    assert recognizer.energy_threshold < 300  # Silence was actually sampled.
    assert recognizer.dynamic_energy_threshold is False
    assert len(pcm) > 16000 * 2


def test_manual_energy_threshold_skips_calibration_and_keeps_quiet_speech():
    ambient = SyntheticMicrophone(synthetic_audio([(1, 0)]))
    ambient.read = Mock(wraps=ambient.read)
    speech = SyntheticMicrophone(synthetic_audio([(1, 150), (3, 0)]))
    recognizer = sr.Recognizer()
    with (
        patch("iris_droid.client.open_microphone", side_effect=[ambient, speech]),
        patch("speech_recognition.Recognizer", return_value=recognizer),
        patch("iris_droid.client.play_wav"),
    ):
        pcm, rate = read_wav(record_turn(1, 30, 10, None, energy_threshold=125))
    ambient.read.assert_not_called()
    assert recognizer.energy_threshold == 125
    assert recognizer.dynamic_energy_threshold is False
    assert np.count_nonzero(np.frombuffer(pcm, dtype="<i2") == 150) / rate > 0.9


@pytest.mark.parametrize("local", [False, True])
def test_new_capture_options_reach_live_and_local_capture(local):
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": TOKEN}),
        patch("iris_droid.client.record_turn", side_effect=KeyboardInterrupt) as record,
    ):
        argv = [
            "client",
            "--no-sounds",
            "--activation",
            "continuous",
            "--microphone-rate",
            "48000",
            "--energy-threshold",
            "125",
            "--calibration-duration",
            "1.5",
        ]
        if local:
            argv.append("--test-microphone")
        assert iris.main(argv) == 0
    assert record.call_args.kwargs == {
        "sample_rate": 48000,
        "energy_threshold": 125,
        "calibration_duration": 1.5,
        "diagnostics": local,
        "feedback": record.call_args.kwargs["feedback"],
        **({"announce_ready": True} if not local else {}),
    }


def test_local_microphone_test_has_no_network_key_wake_model_or_files(capsys):
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("httpx.Client") as transport,
        patch("iris_droid.client.WakeDetector") as wake,
        patch("iris_droid.client.record_turn", return_value=RECORDING) as record,
        patch("iris_droid.client.play_wav") as play,
        patch("pathlib.Path.write_bytes") as write,
    ):
        assert (
            iris.main(
                [
                    "client",
                    "--no-sounds",
                    "--test-microphone",
                    "--microphone",
                    "1",
                    "--speaker",
                    "7",
                    "--activation",
                    "wake",
                ]
            )
            == 0
        )
    assert record.call_args.args[0] == 1 and record.call_args.args[3] is None
    play.assert_called_once_with(RECORDING, 7)
    transport.assert_not_called()
    wake.assert_not_called()
    write.assert_not_called()
    assert "Recorded 0.1 seconds" in capsys.readouterr().out


def test_local_test_silence_reports_actionable_failure(capsys):
    with (
        patch("iris_droid.client.record_turn", side_effect=sr.WaitTimeoutError()),
        patch("httpx.Client") as transport,
    ):
        assert iris.main(["client", "--no-sounds", "--test-microphone"]) == 1
    assert "Check input gain and speech threshold" in capsys.readouterr().out
    transport.assert_not_called()


@pytest.mark.parametrize(
    "flag,keyword,value",
    [
        ("--microphone-rate", "sample_rate", 7999),
        ("--microphone-rate", "sample_rate", 192001),
        ("--energy-threshold", "energy_threshold", 0),
        ("--energy-threshold", "energy_threshold", -1),
        ("--energy-threshold", "energy_threshold", 32769),
        ("--energy-threshold", "energy_threshold", float("nan")),
        ("--energy-threshold", "energy_threshold", float("inf")),
        ("--calibration-duration", "calibration_duration", 0.09),
        ("--calibration-duration", "calibration_duration", 10.1),
        ("--calibration-duration", "calibration_duration", float("nan")),
        ("--calibration-duration", "calibration_duration", float("inf")),
    ],
)
def test_invalid_capture_options_fail_before_audio_or_network(flag, keyword, value):
    with (
        patch("iris_droid.client.open_microphone") as microphone,
        patch("httpx.Client") as transport,
    ):
        assert iris.main(["client", "--no-sounds", flag, str(value)]) == 1
        with pytest.raises(ValueError):
            record_turn(1, 30, 10, None, **{keyword: value})
    microphone.assert_not_called()
    transport.assert_not_called()


@pytest.mark.parametrize("failure", [OSError("Device unavailable"), KeyboardInterrupt()])
def test_local_test_audio_failure_or_interrupt_never_contacts_server(failure):
    with (
        patch("iris_droid.client.record_turn", side_effect=failure),
        patch("httpx.Client") as transport,
    ):
        assert iris.main(["client", "--no-sounds", "--test-microphone"]) == (
            0 if isinstance(failure, KeyboardInterrupt) else 1
        )
    transport.assert_not_called()


def test_local_microphone_test_rejects_text_mode_without_opening_audio():
    with (
        patch("iris_droid.client.record_turn") as record,
        patch("httpx.Client") as transport,
    ):
        assert iris.main(["client", "--no-sounds", "--test-microphone", "--text"]) == 1
    record.assert_not_called()
    transport.assert_not_called()
