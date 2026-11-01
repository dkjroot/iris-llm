"""Wake-window state and control utterances, without a microphone or models."""

from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest
import speech_recognition as sr

import iris
from iris_droid.capture import CaptureCalibration
from iris_droid.client import DroidClient, record_turn
from iris_droid.conversation import WakeConversation
from iris_droid.protocol import TurnReply, is_stop_listening, write_wav
from iris_droid.server import Brain
from iris_settings import Settings

RECORDING = write_wav(b"\x01\x00" * 1600, 16000)


@pytest.mark.parametrize("text_only", [False, True])
@pytest.mark.parametrize("wake_conversation", [False, True])
def test_clear_context_resets_only_current_session_and_next_request_is_fresh(
    text_only, wake_conversation
):
    import json

    brain, transport, requests = make_brain()
    with transport:
        brain.turn("pi", b"old question", text_only=True)
        brain.turn("other", b"other question", text_only=True)
        other_history = brain.sessions["other"].history.copy()
        brain.stt.recognize.return_value = "  CLEAR   Context! "
        reply = brain.turn(
            "pi",
            b"  CLEAR   Context! " if text_only else RECORDING,
            text_only=text_only,
            wake_conversation=wake_conversation,
        )
        assert "pi" not in brain.sessions
        assert brain.sessions["other"].history == other_history
        assert not reply.audio and not reply.finished
        assert "Context cleared" in reply.text
        assert len(requests) == 2
        brain.tts.synthesize.assert_not_called()
        brain.turn("pi", b"new question", text_only=True)
        messages = json.loads(requests[-1].content)["messages"]
        assert [m["role"] for m in messages] == ["system", "user"]
        assert messages[-1]["content"] == "new question"
        assert messages[0]["content"].startswith(brain.settings.system_prompt)


def test_clear_without_existing_session_is_acknowledged_without_creating_history():
    brain, transport, requests = make_brain()
    with transport:
        reply = brain.turn("pi", b"clear context", text_only=True)
    assert "Context cleared" in reply.text
    assert not requests and not brain.sessions
    brain.tts.synthesize.assert_not_called()


@pytest.mark.parametrize(
    "phrase", ["please clear context", "clear context now", "don't clear context"]
)
def test_clear_context_in_longer_message_is_normal_chat(phrase):
    brain, transport, requests = make_brain()
    with transport:
        brain.turn("pi", phrase.encode(), text_only=True)
    assert len(requests) == 1
    assert brain.sessions["pi"].history[0].content == phrase


def test_open_wake_conversation_reuses_calibration_then_refreshes_after_sleep():
    calibration = CaptureCalibration()
    recognizers = [sr.Recognizer() for _ in range(3)]
    for recognizer in recognizers:
        recognizer.adjust_for_ambient_noise = Mock()
        recognizer.listen = Mock(return_value=sr.AudioData(b"\x01\x00" * 1600, 16000, 2))
    recognizers[0].energy_threshold = 125
    recognizers[2].energy_threshold = 225
    source = Mock(SAMPLE_RATE=44100, CHUNK=8192)
    with (
        patch("iris_droid.client.open_microphone") as microphone,
        patch("speech_recognition.Recognizer", side_effect=recognizers),
        patch("iris_droid.client.play_wav"),
    ):
        microphone.return_value.__enter__.return_value = source
        record_turn(1, 30, 10, Mock(), calibration=calibration)
        assert microphone.call_count == 3  # Calibration, wake waiting, speech.
        record_turn(1, 30, 10, None, calibration=calibration)
        assert microphone.call_count == 4  # Only speech capture on subsequent turns.
        assert recognizers[1].energy_threshold == 125
        recognizers[1].adjust_for_ambient_noise.assert_not_called()
        record_turn(1, 30, 10, Mock(), calibration=calibration)
        assert microphone.call_count == 7
        assert calibration.energy_threshold == 225
        recognizers[2].adjust_for_ambient_noise.assert_called_once()


def test_window_opens_refreshes_expires_and_sleeps_with_monotonic_time():
    now = [10.0]
    window = WakeConversation(300, clock=lambda: now[0])
    assert not window.active and window.remaining() == 0
    window.activate()
    now[0] = 110
    assert window.remaining() == 200
    window.activate()
    now[0] = 409
    assert window.remaining() == 1
    now[0] = 410
    assert window.remaining() == 0
    window.sleep()
    assert not window.active and window.remaining() == 0


@pytest.mark.parametrize("phrase", ["Stop listening", "STOP LISTENING!", "  stop  listening. "])
def test_stop_control_accepts_case_spacing_and_terminal_punctuation(phrase):
    assert is_stop_listening(phrase)


@pytest.mark.parametrize(
    "phrase", ["don't stop listening", "please stop listening", "stop listening now"]
)
def test_stop_control_does_not_match_a_longer_chat_message(phrase):
    assert not is_stop_listening(phrase)


def make_brain():
    requests = []

    def chat(request):
        requests.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": "hello"}}]}
        )

    transport = httpx.Client(transport=httpx.MockTransport(chat))
    stt = Mock(recognize=Mock(return_value="hello"))
    tts = Mock(synthesize=Mock(return_value=(np.ones(100, dtype=np.float32), 24000)))
    return Brain(Settings(), transport, stt, tts), transport, requests


def test_stop_control_bypasses_chat_synthesis_and_preserves_existing_history():
    brain, transport, requests = make_brain()
    with transport:
        brain.turn("pi", RECORDING, wake_conversation=True)
        previous = brain.sessions["pi"].history.copy()
        brain.stt.recognize.return_value = "Stop listening."
        reply = brain.turn("pi", RECORDING, wake_conversation=True)
        assert reply.heard == "Stop listening." and not reply.audio and not reply.finished
        assert brain.sessions["pi"].history == previous
        assert len(requests) == 1 and brain.tts.synthesize.call_count == 1
        brain.stt.recognize.return_value = "hello again"
        brain.turn("pi", RECORDING, wake_conversation=True)
        assert len(brain.sessions["pi"].history) == 4


def test_stop_before_first_chat_does_not_create_a_session():
    brain, transport, requests = make_brain()
    with transport:
        brain.stt.recognize.return_value = "stop listening"
        brain.turn("pi", RECORDING, wake_conversation=True)
    assert not brain.sessions and not requests
    brain.tts.synthesize.assert_not_called()


def test_stop_is_an_ordinary_chat_message_outside_wake_voice_mode():
    brain, transport, requests = make_brain()
    with transport:
        brain.stt.recognize.return_value = "Stop listening"
        brain.turn("pi", RECORDING)
        brain.turn("text", b"stop listening", text_only=True, wake_conversation=True)
    assert len(requests) == 2


def test_wake_control_header_is_opt_in_and_does_not_change_response_schema():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=TurnReply(heard="hello", text="hello").model_dump())

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        DroidClient("http://pc:8765", "test", transport).turn(RECORDING)
        DroidClient("http://pc:8765", "test", transport, wake_conversation=True).turn(RECORDING)
    assert "X-Iris-Wake-Conversation" not in requests[0].headers
    assert requests[1].headers["X-Iris-Wake-Conversation"] == "1"


def test_client_requires_one_wake_then_sleeps_on_stop_and_requires_wake_again(capsys):
    waiting = []
    responses = iter(
        [
            TurnReply(heard="hello", text="hello"),
            TurnReply(heard="another sentence", text="hello"),
            TurnReply(
                heard="Clear context.", text="Context cleared. Ready for a fresh conversation."
            ),
            TurnReply(heard="Stop listening.", text="Waiting for the wake phrase."),
            TurnReply(heard="exit", text="goodbye", finished=True),
        ]
    )

    def record(*args, **kwargs):
        waiting.append(args[3] is not None)
        return RECORDING

    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("iris_droid.client.WakeDetector"),
        patch("iris_droid.client.record_turn", side_effect=record),
        patch("iris_droid.client.DroidClient.turn", side_effect=lambda *a, **kw: next(responses)),
    ):
        assert (
            iris.main(["client", "--no-sounds", "--activation", "wake", "--wake-model", "."]) == 0
        )
    assert waiting == [True, False, False, False, True]
    assert 'Waiting for "hey iris" again.' in capsys.readouterr().out


def test_idle_timeout_returns_to_local_wake_without_request_or_error_beep(capsys):
    now = [0.0]
    window = WakeConversation(300, clock=lambda: now[0])
    waiting = []

    def record(*args, **kwargs):
        waiting.append(args[3] is not None)
        if len(waiting) == 1:
            return RECORDING
        if len(waiting) == 2:
            assert window.remaining() == 300
            now[0] += 300
            raise sr.WaitTimeoutError()
        raise KeyboardInterrupt()

    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("iris_droid.client.WakeConversation", return_value=window),
        patch("iris_droid.client.WakeDetector"),
        patch("iris_droid.client.record_turn", side_effect=record),
        patch("iris_droid.client.ClientFeedback.error") as error,
        patch(
            "iris_droid.client.DroidClient.turn",
            return_value=TurnReply(heard="hello", text="hello"),
        ) as turn,
    ):
        assert (
            iris.main(["client", "--no-sounds", "--activation", "wake", "--wake-model", "."]) == 0
        )
    assert waiting == [True, False, True]
    turn.assert_called_once()
    error.assert_not_called()
    assert "Conversation idle" in capsys.readouterr().out


def test_long_model_reply_does_not_use_up_idle_window():
    now = [0.0]
    window = WakeConversation(300, clock=lambda: now[0])
    captures = 0

    def record(*args, **kwargs):
        nonlocal captures
        captures += 1
        if captures == 2:
            assert args[3] is None and window.remaining() == 300
            raise KeyboardInterrupt()
        return RECORDING

    def turn(*args, **kwargs):
        now[0] += 700
        return TurnReply(heard="hello", text="hello")

    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("iris_droid.client.WakeConversation", return_value=window),
        patch("iris_droid.client.WakeDetector"),
        patch("iris_droid.client.record_turn", side_effect=record),
        patch("iris_droid.client.DroidClient.turn", side_effect=turn),
    ):
        assert (
            iris.main(["client", "--no-sounds", "--activation", "wake", "--wake-model", "."]) == 0
        )


@pytest.mark.parametrize("with_wake", [False, True])
def test_real_capture_waits_for_remaining_window_and_opens_it_after_wake(with_wake):
    now = [0.0]
    window = WakeConversation(300, clock=lambda: now[0])
    if not with_wake:
        window.activate()
        now[0] += 10
    source = Mock(SAMPLE_RATE=44100, CHUNK=8192)
    recognizer = Mock()
    recognizer.listen.return_value = sr.AudioData(b"\x01\x00" * 1600, 16000, 2)
    with (
        patch("iris_droid.client.open_microphone") as microphone,
        patch("speech_recognition.Recognizer", return_value=recognizer),
        patch("iris_droid.client.play_wav"),
    ):
        microphone.return_value.__enter__.return_value = source
        record_turn(1, 30, 10, Mock() if with_wake else None, conversation=window)
    assert recognizer.listen.call_args.kwargs["timeout"] == (300 if with_wake else 290)
    assert window.active


@pytest.mark.parametrize("value", [0, -1, 3601, float("nan"), float("inf")])
def test_invalid_idle_setting_fails_before_audio_or_transport(value):
    with (
        patch("httpx.Client") as transport,
        patch("iris_droid.client.open_microphone") as microphone,
    ):
        assert iris.main(["client", "--conversation-timeout", str(value)]) == 1
    transport.assert_not_called()
    microphone.assert_not_called()


@pytest.mark.parametrize("phrase", ["Stop listening.", "Clear context."])
def test_http_wake_control_reaches_server_without_chat_or_synthesis(phrase):
    import threading
    from http.server import HTTPServer

    from iris_droid.server import handler_for

    brain, transport, requests = make_brain()
    brain.stt.recognize.return_value = phrase
    with transport, HTTPServer(("127.0.0.1", 0), handler_for(brain, "test")) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with httpx.Client(trust_env=False, timeout=5) as http:
                client = DroidClient(
                    f"http://127.0.0.1:{server.server_port}", "test", http, wake_conversation=True
                )
                reply = client.turn(RECORDING)
                assert reply.heard == phrase and not reply.audio
                assert not requests and not brain.sessions
                brain.tts.synthesize.assert_not_called()
        finally:
            server.shutdown()
            worker.join(timeout=5)
