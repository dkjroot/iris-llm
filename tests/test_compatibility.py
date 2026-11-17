"""A small public-behaviour oracle for the forthcoming orchestration refactor."""

import json
from pathlib import Path

import pytest

from iris_chat_engines.messages import Message
from iris_droid.feedback import ClientFeedback
from iris_droid.personalities import personality_command
from iris_droid.protocol import (
    companion_command,
    is_clear_context,
    is_delete_previous,
    is_stop_listening,
    write_wav,
)
from iris_droid.volume import volume_command
from iris_memory.controls import memory_command
from iris_memory.details import detail_command

CASES = json.loads((Path(__file__).parent / "fixtures" / "command_contract.json").read_text())


def classify(phrase):
    if is_clear_context(phrase):
        return "clear"
    if is_delete_previous(phrase):
        return "delete_previous"
    if is_stop_listening(phrase):
        return "stop"
    if (mode := companion_command(phrase)) is not None:
        return f"companion:{mode}"
    if (action := memory_command(phrase)) is not None:
        return f"memory:{action}"
    if (detail := detail_command(phrase)) is not None:
        return f"detail:{detail.action}"
    if (personality := personality_command(phrase)) is not None:
        return "personality:" + personality
    if (volume := volume_command(phrase)) is not None:
        return f"volume:{volume.action}:{volume.value}"
    return None


@pytest.mark.parametrize("case", CASES, ids=[row["phrase"] for row in CASES])
def test_exact_command_grammar(case):
    assert classify(case["phrase"]) == case["result"]


@pytest.mark.parametrize("text_only", [True, False], ids=["text", "voice"])
@pytest.mark.parametrize("saving,private", [(False, False), (True, False), (True, True)])
def test_turn_history_and_archive_contract(brain_factory, text_only, saving, private):
    harness = brain_factory(memory_enabled=saving)
    brain = harness.brain
    if private:
        brain.turn("client", b"private mode on", text_only=True)
    reply = brain.turn(
        "client",
        b"Hello Iris" if text_only else write_wav(b"\1\0" * 1600, 16000),
        text_only=text_only,
    )
    assert (reply.heard, reply.text, reply.finished) == ("Hello Iris", "Hello.", False)
    assert bool(reply.audio) is not text_only
    assert harness.stt.calls == (0 if text_only else 1)
    assert harness.tts.calls == ([] if text_only else ["Hello."])
    assert len(brain.sessions["client"].history) == 2
    if saving:
        assert len(brain.memory.export("default")["exchanges"]) == (0 if private else 1)
    else:
        assert not brain.settings.memory_database.exists()


def test_shared_owner_clear_rotates_archive_without_erasing_source(brain_factory):
    harness = brain_factory(
        memory_enabled=True,
        replies=[
            Message(role="assistant", content="First reply."),
            Message(role="assistant", content="Second reply."),
            Message(role="assistant", content='{"notes":[]}'),
            Message(role="assistant", content="Fresh reply."),
        ],
    )
    brain = harness.brain
    brain.turn("one", b"First message", text_only=True)
    brain.turn("two", b"Second message", text_only=True)
    assert any(m.content == "First message" for m in harness.model.calls[1][0])
    before = brain.memory.current_conversation("default")
    brain.turn("one", b"clear context", text_only=True)
    assert brain.memory.current_conversation("default") != before
    brain.turn("two", b"Fresh message", text_only=True)
    assert not any(m.content == "First message" for m in harness.model.calls[-1][0])
    assert [e["user_text"] for e in brain.memory.export("default")["exchanges"]] == [
        "First message",
        "Second message",
        "Fresh message",
    ]


def test_cancellation_confirmation_survives_no_sounds():
    played = []
    feedback = ClientFeedback(
        None, lambda data, device: played.append(data), lambda: None, enabled=False
    )
    feedback.ready()
    feedback.cancelled()
    assert len(played) == 1 and played[0].startswith(b"RIFF")
