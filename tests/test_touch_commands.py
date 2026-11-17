"""Touch requests use the voice client's session and interrupt waiting capture safely."""

import json
from contextlib import contextmanager
from threading import Event
from unittest.mock import Mock

import httpx
import pytest

from iris_droid import client
from iris_droid.capture import CaptureInterrupted, CheckedMicrophoneStream
from iris_droid.companion import ConsoleInput
from iris_droid.face_display import FaceDisplay
from iris_droid.touch_commands import TouchCommands
from tests.test_face import model_response, running_brain


def test_queue_accepts_one_allowed_command_and_serializes_with_voice():
    queue = TouchCommands()
    assert not queue.submit("Companion mode on")
    queue.listen()
    assert not queue.submit("Remember LABEL as VALUE")
    assert queue.submit("Companion mode on")
    assert queue.interrupt.is_set() and not queue.ready
    assert not queue.submit("Companion mode off")
    assert not queue.begin_voice()
    assert queue.take() == "Companion mode on"
    assert not queue.interrupt.is_set() and not queue.ready
    assert not queue.submit("Companion mode off")
    queue.listen()
    assert queue.begin_voice()
    assert not queue.submit("Mute")
    queue.close()
    assert not queue.ready


def test_capture_interrupt_discards_the_buffer_and_terminal_input_is_not_duplicated(monkeypatch):
    interrupt = Event()
    stream = Mock()
    stream.pyaudio_stream.read.side_effect = lambda *args, **kwargs: (interrupt.set(), b"\0\0")[1]
    checked = CheckedMicrophoneStream(stream, interrupt)
    with pytest.raises(CaptureInterrupted):
        checked.read(1)
    release = Event()
    started = Event()

    def line(prompt):
        started.set()
        assert release.wait(3)
        return "hello"

    reader = Mock(side_effect=line)
    monkeypatch.setattr("builtins.input", reader)
    console = ConsoleInput()
    try:
        with pytest.raises(CaptureInterrupted):
            console.read("You: ", interrupt=interrupt)
        assert started.wait(1)
        interrupt.clear()
        release.set()
        assert console.read("You: ", timeout=2, interrupt=interrupt) == "hello"
        reader.assert_called_once()
    finally:
        release.set()


def test_local_command_endpoint_rejects_templates_foreign_origin_and_duplicate_taps():
    display = FaceDisplay("signal")
    queue = TouchCommands()
    display.execute_command = queue.submit
    display.commands_ready = lambda: queue.ready
    with (
        display.serve(0) as port,
        httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False) as http,
    ):
        assert http.post("/command", json="Companion mode on").status_code == 409
        queue.listen()
        assert http.get("/state").json()["commands_ready"]
        assert http.post("/command", json="Remember LABEL as VALUE").status_code == 400
        assert (
            http.post(
                "/command", json="Mute", headers={"Origin": "https://elsewhere.test"}
            ).status_code
            == 403
        )
        assert queue.ready
        assert http.post("/command", json="Companion mode on").status_code == 204
        assert http.post("/command", json="Companion mode on").status_code == 409
        assert not http.get("/state").json()["commands_ready"]
        assert queue.take() == "Companion mode on"


@pytest.mark.parametrize("activation", ["continuous", "wake"])
def test_tap_interrupts_waiting_capture_and_updates_same_session_companion_timer(
    monkeypatch, activation
):
    displays = []
    original = FaceDisplay.serve
    turns = []
    original_turn = client.DroidClient.turn

    @contextmanager
    def serve(display, port):
        displays.append(display)
        with original(display, 0) as actual:
            yield actual

    def turn(self, body, **options):
        assert not displays[0].commands_ready()
        assert not displays[0].execute_command("Mute")
        result = original_turn(self, body, **options)
        turns.append((self.headers["X-Iris-Session"], body, options, result))
        return result

    captures = 0

    def record(*args, **kwargs):
        nonlocal captures
        captures += 1
        assert (kwargs["companion_timer"] is not None) == (captures == 2)
        if captures == 3:
            assert json.loads(displays[0].state())["notice"]["title"] == "Companion mode off"
            raise KeyboardInterrupt
        assert displays[0].execute_command(
            "Companion mode on" if captures == 1 else "Companion mode off"
        )
        assert kwargs["interrupt"].is_set()
        raise CaptureInterrupted

    monkeypatch.setattr(FaceDisplay, "serve", serve)
    monkeypatch.setattr(client.DroidClient, "turn", turn)
    monkeypatch.setattr(client, "record_turn", record)
    monkeypatch.setattr(client, "WakeDetector", Mock())
    monkeypatch.setenv("IRIS_DROID_TOKEN", "test-only-droid-key-for-the-face")
    with running_brain(lambda _: model_response("unexpected model reply"), Mock()) as (brain, url):
        args = ["--server", url, "--face", "signal", "--no-sounds", "--activation", activation]
        if activation == "wake":
            args += ["--wake-model", "."]
        assert client.main(args) == 0
        assert len(turns) == 2
        assert turns[0][0] == turns[1][0]
        assert all(options == {"text_only": True} for _, _, options, _ in turns)
        assert [result.command.status for _, _, _, result in turns] == ["success", "success"]
        brain.stt.recognize.assert_not_called()
    assert not displays[0].commands_ready()
