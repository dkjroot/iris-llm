"""Commands produce deterministic receipts; model prose never proves a change."""

import json
from unittest.mock import Mock
from uuid import uuid4

import httpx
import numpy as np
import pytest

from iris_domain.commands import companion_command
from iris_domain.replies import COMMAND_FEEDBACK_HEADER
from iris_droid.client import DroidClient
from iris_droid.face_console import read_state
from iris_droid.face_display import FaceDisplay
from iris_droid.protocol import COMPLETION_HEADER, VOICE_STREAM_TYPE
from tests.test_companion import make_brain
from tests.test_face import RECORDING, TOKEN, model_response, running_brain


@pytest.mark.parametrize(
    "phrase",
    [
        "Please turn companion mode on.",
        "Could you please enable companion mode?",
        "Switch on companion mode, please.",
        "Companion mode, on!",
        "Turn the companion mode on",
        "start companion mode",
    ],
)
def test_polite_companion_commands_bypass_the_model(phrase):
    brain, transport, requests = make_brain()
    with transport:
        brain.stt.recognize.return_value = phrase
        result = brain.turn("pi", RECORDING)
        assert result.companion_mode and result.audio
        assert result.command.status == "success"
        assert result.command.title == "Companion mode on"
        assert requests == []
        assert not brain.sessions["pi"].history


@pytest.mark.parametrize(
    "phrase",
    [
        "don't turn companion mode on",
        "do not enable companion mode",
        "what happens if I say companion mode on",
        'say "companion mode on"',
        "please don't enable companion mode",
        "companion mode on then delete previous",
    ],
)
def test_embedded_negated_and_combined_commands_do_not_switch_mode(phrase):
    assert companion_command(phrase) is None


def test_status_does_not_create_or_change_a_session_and_reply_text_is_not_a_receipt():
    brain, transport, requests = make_brain()
    with transport:
        off = brain.turn("pi", b"Is companion mode on?", text_only=True)
        assert off.command.status == "info" and off.text == "Companion mode is off."
        assert not brain.sessions and not requests
        brain.turn("pi", b"companion mode on", text_only=True)
        on = brain.turn("pi", b"companion mode status", text_only=True)
        assert on.text == "Companion mode is on." and on.companion_mode
        assert not requests
        ordinary = brain.turn("pi", b"hello", text_only=True)
        assert ordinary.command is None


def test_failed_speech_preparation_does_not_enable_companion_mode():
    brain, transport, requests = make_brain()
    with transport:
        brain.tts.synthesize.side_effect = RuntimeError("synthetic speech failure")
        with pytest.raises(RuntimeError):
            brain.turn("pi", RECORDING)
        assert not brain.sessions["pi"].companion_mode
        assert not requests


@pytest.mark.parametrize("stream", [False, True])
def test_negotiated_receipts_are_final_and_old_clients_keep_the_old_shape(stream):
    tts = Mock(synthesize=Mock(return_value=(np.ones(100, dtype=np.float32), 24000)))
    tts.stream = lambda _: (chunk for chunk in [b"\1\0" * 100])
    tts.sample_rate = 24000
    with (
        running_brain(lambda _: model_response("Companion mode is on."), tts, stream=stream) as (
            brain,
            url,
        ),
        httpx.Client(trust_env=False, timeout=5) as http,
    ):
        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "X-Iris-Session": str(uuid4()),
            "Content-Type": "audio/wav",
            "Accept": VOICE_STREAM_TYPE,
            COMPLETION_HEADER: "1",
        }
        brain.stt.recognize.return_value = "companion mode status"
        legacy = http.post(url + "/v1/voice", content=RECORDING, headers=headers)
        current = http.post(
            url + "/v1/voice", content=RECORDING, headers={**headers, COMMAND_FEEDBACK_HEADER: "1"}
        )
        if stream:
            old_frames = [json.loads(line) for line in legacy.text.splitlines()]
            frames = [json.loads(line) for line in current.text.splitlines()]
            assert "command" not in old_frames[-1]["completion"]
            assert "command" not in frames[0]["reply"]
            assert frames[-1]["completion"]["command"]["status"] == "info"
        else:
            assert "command" not in legacy.json()
            assert current.json()["command"]["status"] == "info"
        client = DroidClient(
            url, TOKEN, http, stream_player=lambda start, chunks: list(chunks) and None
        )
        result = client.turn(RECORDING)
        assert result.command.title == "Companion mode status"
        brain.stt.recognize.return_value = "hello"
        result = client.turn(RECORDING)
        assert result.text == "Companion mode is on." and result.command is None
        assert not result.companion_mode


def test_native_notice_expires_without_losing_mode_status(monkeypatch, fake_clock):
    monkeypatch.setattr("iris_droid.face_display.monotonic", fake_clock.monotonic)
    display = FaceDisplay("rivet")
    display.companion_mode = True
    display.notify("Companion mode on", "Enabled for this conversation.", "success")
    with display.serve(0) as port:
        state = read_state(port)
        assert state.notice == ("Companion mode on", "Enabled for this conversation.", "success")
        assert state.companion_mode is True
        fake_clock.advance(6.1)
        state = read_state(port)
        assert state.notice is None and state.companion_mode is True


@pytest.mark.parametrize(
    "phrase",
    [
        "clear context",
        "delete previous",
        "companion mode on",
        "companion mode off",
        "companion mode status",
        "private mode on",
        "private mode off",
        "memory status",
        "forget this conversation",
        "forget that conversation",
        "confirm forget this conversation",
        "cancel forgetting",
        "list remembered details",
        "remember colour as blue",
        "show the detail colour",
        "correct colour to red",
        "forget the detail colour",
        "show that detail",
        "forget that detail",
        "confirm memory change",
        "cancel memory change",
        "don't bring that up again",
        "list personalities",
        "switch to personality absent",
        "volume up",
        "exit",
    ],
)
def test_every_builtin_command_has_a_visual_receipt_from_voice(brain_factory, phrase):
    harness = brain_factory(memory_enabled=True)
    harness.brain.stt = Mock(recognize=Mock(return_value=phrase))
    reply = harness.brain.turn("pi", RECORDING)
    assert reply.command is not None
    assert reply.command.title and reply.command.message
    assert reply.command.status in {"info", "success", "error"}


def test_memory_receipts_distinguish_request_from_committed_change(brain_factory):
    brain = brain_factory(memory_enabled=True).brain
    request = brain.turn("pi", b"remember colour as blue", text_only=True)
    assert request.command.title == "Confirmation needed"
    assert request.command.status == "info"
    assert "confirm memory change" in request.command.message
    applied = brain.turn("pi", b"confirm memory change", text_only=True)
    assert applied.command.status == "success" and applied.command.title == "Memory updated"
    assert brain.details.items("default", "colour")[0]["text"] == "blue"
    stale = brain.turn("pi", b"confirm memory change", text_only=True)
    assert stale.command.status != "success"
    assert "no current" in stale.command.message


def test_recall_clarification_and_stop_listening_have_receipts(brain_factory):
    brain = brain_factory(memory_enabled=True).brain
    brain.turn("pi", b"hello", text_only=True)
    recall = brain.turn("pi", b"Recall the garden project last week", text_only=True)
    assert recall.command.title == "Memory recall"
    assert recall.command.status == "info"
    brain.memory_context = None
    brain.turn("pi", b"companion mode on", text_only=True)
    brain.stt = Mock(recognize=Mock(return_value="stop listening"))
    stopped = brain.turn("pi", RECORDING)
    assert stopped.command.status == "success" and not stopped.companion_mode
