import base64
import json
import sys
from http.server import ThreadingHTTPServer
from importlib.resources import files
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock, call
from uuid import uuid4

import httpx
import numpy as np
import pytest

from iris_chat_engines.messages import FunctionCall, Message, ToolCall
from iris_droid.client import DroidClient
from iris_droid.feedback import ClientFeedback
from iris_droid.protocol import TurnCancelled, write_wav
from iris_droid.server import Brain, handler_for
from iris_droid.wake import WakeDetector
from iris_settings import Settings

TOKEN = "voice-cancel-test-token-long-enough"
RECORDING = write_wav(b"\x01\x00" * 1600, 16000)
ACCIDENT = "An accidental private sentence."


@pytest.fixture
def voice_server(tmp_path):
    settings = Settings(
        memory_enabled=True,
        memory_database=tmp_path / "memory.db",
        memory_hierarchy_enabled=False,
        memory_semantic_enabled=False,
    )
    stt = Mock(recognize=Mock(return_value=ACCIDENT))
    tts = Mock(synthesize=Mock(return_value=(np.zeros(100, dtype=np.float32), 24000)))
    with httpx.Client() as backend, httpx.Client(trust_env=False, timeout=5) as client:
        brain = Brain(settings, backend, stt, tts)
        chat = brain._new_chat()
        chat.backend = Mock(complete=Mock(return_value=Message(role="assistant", content="Hello")))
        brain._new_chat = Mock(return_value=chat)
        with ThreadingHTTPServer(("127.0.0.1", 0), handler_for(brain, TOKEN)) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield brain, chat, client, f"http://127.0.0.1:{server.server_port}"
            finally:
                server.shutdown()
                thread.join(timeout=5)


@pytest.mark.parametrize("sounds_enabled", [True, False])
def test_cancel_returns_while_transcription_is_still_running(voice_server, sounds_enabled):
    brain, chat, client, url = voice_server
    recognizing = Event()
    release = Event()

    def recognize(audio):
        recognizing.set()
        assert release.wait(10)
        return ACCIDENT

    brain.stt.recognize.side_effect = recognize

    def listen(stop):
        return recognizing.wait(3)

    audio = Mock()
    feedback = ClientFeedback(7, audio.play, audio.stop, enabled=sounds_enabled)
    droid = DroidClient(url, TOKEN, client, cancel_listener=listen, cancel_feedback=feedback)
    try:
        with pytest.raises(TurnCancelled):
            droid.turn(RECORDING)
        assert not release.is_set()
        assert brain.memory.current_conversation("default") is None
        chat.backend.complete.assert_not_called()
        expected = []
        if sounds_enabled:
            working = files("iris_sounds").joinpath("workinglongquiet.wav").read_bytes()
            expected.extend([call.play(working, 7, loop=True), call.stop()])
        cancelled = files("iris_sounds").joinpath("cancelled.wav").read_bytes()
        assert audio.mock_calls == [*expected, call.play(cancelled, 7)]
    finally:
        release.set()

    # The next request only runs after the abandoned work has unwound.
    brain.stt.recognize.side_effect = None
    brain.stt.recognize.return_value = "A deliberate next turn."

    def keep_next_turn(stop):
        assert stop.wait(3)
        return False

    droid.cancel_listener = keep_next_turn
    assert droid.turn(RECORDING).heard == "A deliberate next turn."
    exchanges = brain.memory.export("default")["exchanges"]
    assert [exchange["user_text"] for exchange in exchanges] == ["A deliberate next turn."]
    assert ACCIDENT not in str(chat.backend.complete.call_args)


@pytest.mark.parametrize("streaming", [False, True])
def test_cancel_at_acceptance_boundary_prevents_reply_and_rolls_back(voice_server, streaming):
    brain, chat, client, url = voice_server
    playback = Mock(return_value=None)
    synthesis_closed = Event()

    def stream(text):
        try:
            yield b"\x01\x00" * 100
        finally:
            synthesis_closed.set()

    if streaming:
        brain.settings.tts_engine = "pocket"
        brain.tts.stream.side_effect = stream
        brain.tts.sample_rate = 24000

    cancel_next = False

    def listen(stop):
        assert stop.wait(3)
        return cancel_next

    def play(start, chunks):
        playback(start, list(chunks))

    droid = DroidClient(
        url,
        TOKEN,
        client,
        cancel_listener=listen,
        stream_player=play if streaming else None,
        trace_voice=True,
    )
    brain.stt.recognize.return_value = "Keep this turn."
    assert droid.turn(RECORDING).text == "Hello"
    if streaming:
        playback.assert_called_once()
        assert synthesis_closed.is_set()
        playback.reset_mock()
        synthesis_closed.clear()
    saved_history = chat.history.copy()

    brain.stt.recognize.return_value = ACCIDENT
    cancel_next = True
    with pytest.raises(TurnCancelled):
        droid.turn(RECORDING)
    playback.assert_not_called()

    # A non-cancellable request queues behind rollback and sees only the accepted turn.
    droid.cancel_listener = None
    # Exercise the wire with a text turn that never changes conversation history.
    droid.turn(b"memory status", text_only=True)
    assert chat.history == saved_history
    assert ACCIDENT not in json.dumps(brain.memory.export("default"))
    if streaming:
        assert synthesis_closed.is_set()


def test_cancellation_prevents_tool_dispatch(voice_server):
    brain, chat, client, url = voice_server
    chat.tools.execute = Mock()
    chat.backend.complete.return_value = Message(
        role="assistant",
        tool_calls=[
            ToolCall(
                id="tool",
                type="function",
                function=FunctionCall(name="get_local_time", arguments="{}"),
            )
        ],
    )
    droid = DroidClient(url, TOKEN, client, cancel_listener=lambda stop: stop.wait(3))
    with pytest.raises(TurnCancelled):
        droid.turn(RECORDING)
    chat.tools.execute.assert_not_called()
    brain.tts.synthesize.assert_not_called()
    assert brain.memory.current_conversation("default") is None


def test_delete_previous_voice_command_can_itself_be_cancelled(voice_server):
    brain, chat, client, url = voice_server
    droid = DroidClient(url, TOKEN, client)
    droid.turn(b"Keep until deletion is accepted", text_only=True)
    brain.stt.recognize.return_value = "Delete previous"
    droid.cancel_listener = lambda stop: stop.wait(3)
    with pytest.raises(TurnCancelled):
        droid.turn(RECORDING)
    assert len(brain.memory.export("default")["exchanges"]) == 1

    def accept(stop):
        assert stop.wait(3)
        return False

    droid.cancel_listener = accept
    deleted = droid.turn(RECORDING)
    assert deleted.text == "Previous exchange deleted."
    assert base64.b64decode(deleted.audio) == (
        files("iris_sounds").joinpath("previous-deleted.wav").read_bytes()
    )
    empty = droid.turn(RECORDING)
    assert empty.text == "There's no previous exchange to delete."
    assert base64.b64decode(empty.audio) == (
        files("iris_sounds").joinpath("nothing-to-delete.wav").read_bytes()
    )
    assert brain.memory.export("default")["exchanges"] == []
    chat.backend.complete.assert_called_once()
    brain.tts.synthesize.assert_not_called()


def test_delete_previous_response_identifies_the_browser_messages_to_remove(voice_server):
    _, _, client, url = voice_server
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-Iris-Session": str(uuid4()),
        "Content-Type": "text/plain",
    }
    saved = client.post(f"{url}/v1/text", headers=headers, content=b"An exchange")
    saved.raise_for_status()
    deleted = client.post(f"{url}/v1/text", headers=headers, content=b"delete previous")
    deleted.raise_for_status()
    assert deleted.headers["X-Iris-Deleted-Exchange"] == saved.headers["X-Iris-Exchange"]
    assert "X-Iris-Exchange" not in deleted.headers


def test_local_listener_requires_both_words_and_checks_buffered_audio(monkeypatch, tmp_path):
    recognizer = Mock()
    stop = Event()
    recognizer.AcceptWaveform.side_effect = [True, True, False]
    recognizer.Result.side_effect = ['{"text":"cancel"}', '{"text":"cancel cancel [unk]"}']
    recognizer.FinalResult.return_value = '{"text":"cancel cancel"}'
    factory = Mock(return_value=recognizer)
    monkeypatch.setitem(sys.modules, "vosk", SimpleNamespace(Model=Mock(), KaldiRecognizer=factory))

    read = Mock(return_value=b"\x01\x00" * 2048)

    def read_chunk(size):
        data = read(size)
        if read.call_count == 3:
            stop.set()
        return data

    source = SimpleNamespace(stream=SimpleNamespace(read=read_chunk), CHUNK=8192, SAMPLE_RATE=44100)
    assert WakeDetector(tmp_path, "hey iris").listen_for_cancel(source, stop)
    assert read.call_count == 3
    assert factory.call_args.args[1] == 16000
    assert json.loads(factory.call_args.args[2]) == ["cancel cancel", "[unk]"]
