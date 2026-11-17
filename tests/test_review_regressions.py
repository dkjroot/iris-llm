"""Passing regressions for the defects reproduced during the initial review."""

import json
import sqlite3
from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from iris_droid.client import DroidClient
from iris_droid.protocol import TurnReply, write_wav
from iris_droid.server import STREAM_REPLY
from iris_home.devices import Device
from iris_home.protocol import ScheduleRequest
from iris_home.schedule import Scheduler
from iris_memory import Exchange, SQLiteMemory
from iris_memory.index import MemoryIndex
from iris_memory.models import Note, Summary
from iris_settings import Settings
from iris_stt_engines.google_online import STT
from tests.support import require_resolution

pytestmark = pytest.mark.review_regression


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("command", ["private", "private_off", "detail", "forget"])
def test_failed_control_commit_never_delivers_success(brain_factory, streaming, command):
    harness = brain_factory(memory_enabled=True, replies=[])
    brain = harness.brain
    memory = brain.memory
    assert memory is not None
    if command in {"private", "private_off"}:
        if command == "private_off":
            brain.turn("client", b"private mode on", text_only=True)
        utterance, target, operation = (
            "private mode off" if command == "private_off" else "private mode on",
            brain,
            "_set_private",
        )
    elif command == "detail":
        brain.turn("client", b"remember colour as blue", text_only=True)
        utterance, target, operation = "confirm memory change", brain.details, "apply"
    else:
        conversation = memory.active_conversation("default")
        memory.append("default", conversation, Exchange("source", "human", "A fact.", "Noted."))
        brain.turn("client", b"forget the current conversation", text_only=True)
        utterance, target, operation = "confirm forget this conversation", memory, "forget_current"
    harness.stt.text = utterance
    delivered = []

    def stream(reply, chunks, rate):
        # Consuming the stream models audio already made available to playback.
        assert sum(map(len, chunks)) > 0
        delivered.append(reply.text)

    token = STREAM_REPLY.set(stream if streaming else None)
    try:
        with patch.object(target, operation, side_effect=sqlite3.OperationalError("injected")):
            with pytest.raises(sqlite3.OperationalError, match="injected"):
                brain.turn("client", write_wav(b"\1\0" * 1600, 16000))
    finally:
        STREAM_REPLY.reset(token)
    assert brain.private_mode is (command == "private_off")
    require_resolution(
        not delivered, f"R01: successful {command} confirmation escaped failed commit"
    )


def test_summary_backlog_eventually_includes_sources_beyond_queue_capacity(tmp_path):
    memory = SQLiteMemory(tmp_path / "archive.sqlite3")
    index = MemoryIndex(memory)
    for number in range(65):
        conversation = memory.start_conversation("owner")
        memory.append("owner", conversation, Exchange(str(number), "human", "A fact.", "Noted."))
    active = memory.start_conversation("owner")
    for _ in range(70):  # A broken queue must not hang this regression.
        conversation = index.next_conversation("owner", active)
        if conversation is None:
            break
        job = index.pending("owner", conversation)
        assert job is not None
        source = index.sources("owner", conversation, job["cursor"])[-1]
        assert index.save(
            "owner",
            conversation,
            source["id"],
            job["cursor"],
            source["sequence"],
            Summary(notes=[]),
        )
    with memory.connect() as db:
        remaining = db.execute(
            "SELECT count(*) FROM conversations c WHERE EXISTS "
            "(SELECT 1 FROM exchanges e WHERE e.conversation=c.id) AND NOT EXISTS "
            "(SELECT 1 FROM summaries s WHERE s.conversation=c.id)"
        ).fetchone()[0]
    assert len(memory.export("owner")["exchanges"]) == 65
    require_resolution(remaining == 0, f"R08: {remaining} conversations have stranded summary work")


def test_weekly_recovery_crosses_a_skipped_dst_occurrence(tmp_path, fake_clock, fake_switch):
    fake_clock.set_wall_time(datetime(2026, 3, 21, tzinfo=UTC))
    scheduler = Scheduler(
        tmp_path / "home.sqlite3",
        ZoneInfo("Europe/London"),
        {"lamp": Device(fake_switch, {})},
        clock=fake_clock.now,
    )
    try:
        scheduler.save(
            "lamp",
            ScheduleRequest(
                device="lamp",
                revision=0,
                rules=[{"days": ["sun"], "time": "01:30", "state": "on"}],
            ),
        )
        fake_clock.advance(11 * 86400)
        # Deterministically step the existing worker without starting a thread.
        with scheduler.condition:
            scheduler._next_delay(fake_clock.now())
            scheduler._next_delay(fake_clock.now())
        require_resolution(fake_switch.commands == ["on"], "R09: missed 22 March recovery action")
    finally:
        scheduler.db.close()


def test_newer_touch_selection_survives_older_voice_acknowledgement(tmp_path):
    path = tmp_path / "personalities.json"
    path.write_text(json.dumps({"A": "Personality A", "B": "Personality B"}), encoding="utf-8")

    def delayed_reply(request):
        assert client.select_personality("B")  # A touch event while the earlier request is pending.
        return httpx.Response(
            200,
            json=TurnReply(
                heard="switch to personality A", text="Switched", selected_personality="A"
            ).model_dump(),
        )

    with httpx.Client(transport=httpx.MockTransport(delayed_reply)) as http:
        client = DroidClient("http://iris.invalid", "x" * 32, http, personalities_file=path)
        client.turn(b"switch to personality A", text_only=True)
        require_resolution(client.personality == "B", "R10: newer touch selection was overwritten")


def test_online_recognition_has_a_finite_network_timeout():
    import math

    timeout = STT(Settings(stt_engine="google", allow_remote=True)).recognizer.operation_timeout
    require_resolution(
        isinstance(timeout, (float, int)) and math.isfinite(timeout) and timeout > 0,
        "R04: recognition can wait indefinitely for its online service",
    )


def test_correcting_one_claim_preserves_other_claims_in_the_same_exchange(tmp_path):
    memory = SQLiteMemory(tmp_path / "archive.sqlite3")
    index = MemoryIndex(memory)
    conversation = memory.active_conversation("owner")
    original = "My robot is called Sapphire. The greenhouse needs ventilation."
    memory.append("owner", conversation, Exchange("one", "human", original, "Understood."))

    def note(text, source, supersedes=()):
        return Note(
            kind="user_assertion",
            text=text,
            sources=[source],
            scope="real",
            status="confirmed",
            supersedes=list(supersedes),
        )

    assert index.save(
        "owner",
        conversation,
        "one",
        0,
        1,
        Summary(
            notes=[
                note("Robot named Sapphire.", "one"),
                note("Greenhouse needs ventilation.", "one"),
            ]
        ),
    )
    old = next(n for n in index.notes("owner", conversation) if "Sapphire" in n["text"])
    assert index.recall_sources("owner", ["greenhouse"], None, None)
    memory.append(
        "owner",
        conversation,
        Exchange("two", "human", "Correction: my robot is called Emerald.", "Understood."),
    )
    assert index.save(
        "owner",
        conversation,
        "two",
        1,
        2,
        Summary(
            notes=[
                note("Robot named Emerald.", "two", [old["id"]]),
            ]
        ),
    )
    assert any("Greenhouse" in n["text"] for n in index.notes("owner", conversation))
    assert memory.export("owner")["exchanges"][0]["user_text"] == original
    require_resolution(
        bool(index.recall_sources("owner", ["greenhouse"], None, None)),
        "R37: the unrelated greenhouse claim became unreachable",
    )
