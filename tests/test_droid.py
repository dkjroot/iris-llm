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

from iris_droid.client import DroidClient
from iris_droid.protocol import MAX_BODY, read_wav, write_wav
from iris_droid.server import Brain, handler_for
from iris_droid.wake import WakeDetector
from iris_settings import Settings

TOKEN = "a-test-secret-with-at-least-24-characters"
RECORDING = write_wav(b"\x01\x00" * 1600, 16000)


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
    source = SimpleNamespace(stream=Mock(read=Mock(return_value=b"pcm")), CHUNK=1024)
    with patch.dict(sys.modules, {"vosk": vosk}):
        wake = WakeDetector(tmp_path, "Hey Iris")
        wake.wait(source)
    vosk.Model.assert_called_once_with(str(tmp_path))
    assert source.stream.read.call_count == 3
    assert json.loads(vosk.KaldiRecognizer.call_args.args[2]) == ["hey iris", "[unk]"]
