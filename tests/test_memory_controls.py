"""Private conversations, source-bound forgetting and diagnostic privacy."""

import base64
import json
import sqlite3
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from http.server import HTTPServer
from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest

import iris
from iris_chat_engines.messages import ChatError, Message
from iris_droid.client import DroidClient
from iris_droid.protocol import TurnReply, write_wav
from iris_droid.server import Brain, handler_for
from iris_memory import Exchange, SQLiteMemory
from iris_memory.controls import MemoryPolicyError, memory_command
from iris_memory.index import MemoryIndex
from iris_memory.models import Note, Summary
from iris_memory.semantic import SemanticMemory
from iris_settings import Settings

VOICE = write_wav(b"\x01\x00" * 1600, 16000)


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        memory_enabled=True, memory_database=tmp_path / "memory.sqlite3", trace=True
    )
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        content = (
            '{"notes": []}'
            if payload["messages"][0]["content"].startswith("Summarise these original")
            else "A friendly reply."
        )
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": content}}]}
        )

    transport = httpx.Client(transport=httpx.MockTransport(respond))
    brain = Brain(
        settings,
        transport,
        Mock(recognize=Mock(return_value="voice thought")),
        Mock(synthesize=Mock(return_value=(np.zeros(10, dtype=np.float32), 24000))),
    )
    yield brain, transport, requests
    transport.close()


def turn(brain, text, session="pi", **kwargs):
    return brain.turn(session, text.encode(), text_only=True, **kwargs)


@pytest.mark.parametrize(
    "phrase,expected",
    [
        (" Private MODE on! ", "private_on"),
        ("Start a private conversation.", "private_on"),
        ("End the private conversation", "private_off"),
        ("memory status", "status"),
        ("Forget this conversation.", "forget"),
        ("confirm forget this conversation", "confirm_forget"),
        ("cancel forgetting", "cancel_forget"),
        ("Please forget everything", None),
        ("The story says private mode on", None),
        ("yes", None),
    ],
)
def test_commands_are_complete_utterances(phrase, expected):
    assert memory_command(phrase) == expected


def test_private_reads_no_archive_and_writes_no_transcript_or_derived_data(setup, capsys):
    brain, transport, requests = setup
    turn(brain, "PUBLIC_ANCHOR")
    old = brain.memory.export("default")["exchanges"]
    turn(brain, "private mode on")
    capsys.readouterr()
    with (
        patch.object(
            brain.memory, "active_conversation", side_effect=AssertionError("archive read")
        ),
        patch.object(brain.memory, "recent", side_effect=AssertionError("archive read")),
        patch.object(brain.memory, "append", side_effect=AssertionError("archive write")),
        patch.object(brain.memory_context, "checkpoint", side_effect=AssertionError("summary")),
        patch.object(brain.recall, "resolve", side_effect=AssertionError("recall")),
    ):
        turn(brain, "PRIVATE_MARKER_1729")
        turn(brain, "Remember the earlier discussion?")
        turn(brain, "companion mode on")
        turn(brain, "idle", companion_idle=True)
        turn(brain, "stop listening")
        turn(brain, "clear context")
        turn(brain, "memory status")
        turn(brain, "exit")
    assert brain.memory.export("default")["exchanges"] == old
    assert b"PRIVATE_MARKER_1729" not in brain.memory.path.read_bytes()
    assert "PRIVATE_MARKER_1729" not in capsys.readouterr().out
    private_payloads = [p for p in requests if "PRIVATE_MARKER_1729" in json.dumps(p)]
    assert private_payloads and all("PUBLIC_ANCHOR" not in json.dumps(p) for p in private_payloads)
    assert brain.private_mode and brain.memory.policy("default")[0]


def test_private_is_owner_wide_persistent_and_survives_eviction(setup):
    brain, transport, requests = setup
    turn(brain, "PUBLIC_ANCHOR", "laptop")
    turn(brain, "private mode on", "pi")
    turn(brain, "SECRET_ON_LAPTOP", "laptop")
    assert "PUBLIC_ANCHOR" not in json.dumps(requests[-1])
    for number in range(17):
        turn(brain, "private hello", str(number))
    assert "laptop" not in brain.sessions
    restarted = Brain(brain.settings, transport, brain.stt, brain.tts)
    assert restarted.private_mode
    turn(restarted, "AFTER_RESTART", "new-client")
    assert "PUBLIC_ANCHOR" not in json.dumps(requests[-1])
    assert all(
        "SECRET_ON_LAPTOP" not in json.dumps(row) and "AFTER_RESTART" not in json.dumps(row)
        for row in brain.memory.export("default")["exchanges"]
    )


def test_leaving_private_drops_every_clients_private_context(setup):
    brain, transport, requests = setup
    turn(brain, "PUBLIC_ANCHOR")
    turn(brain, "private mode on")
    turn(brain, "SECRET_ONE")
    turn(brain, "SECRET_TWO", "laptop")
    turn(brain, "private mode off", "laptop")
    turn(brain, "carry on", "pi")
    payload = json.dumps(requests[-1])
    assert "PUBLIC_ANCHOR" in payload
    assert "SECRET_ONE" not in payload and "SECRET_TWO" not in payload
    assert not brain.private_mode
    assert all(
        "SECRET_" not in json.dumps(row) for row in brain.memory.export("default")["exchanges"]
    )


def test_private_commands_do_not_create_saved_conversations(setup):
    brain, transport, requests = setup
    turn(brain, "private mode on")
    turn(brain, "first ever private message")
    turn(brain, "forget this conversation")
    turn(brain, "private mode off")
    assert brain.memory.conversations("default") == []
    assert brain.memory.export("default")["exchanges"] == []
    turn(brain, "now public")
    assert len(brain.memory.export("default")["exchanges"]) == 1


def test_forgetting_requires_exact_confirmation_and_does_not_archive_controls(setup):
    brain, transport, requests = setup
    turn(brain, "a whole saved discussion")
    requests.clear()
    prompt = turn(brain, "forget this conversation")
    assert "1 exchange" in prompt.text
    assert not requests
    assert len(brain.memory.export("default")["exchanges"]) == 1
    turn(brain, "yes")  # Ordinary speech, never a deletion confirmation.
    assert len(brain.memory.export("default")["exchanges"]) == 2
    reply = turn(brain, "confirm forget this conversation")
    assert "no current" in reply.text
    assert len(brain.memory.export("default")["exchanges"]) == 2
    turn(brain, "forget this conversation")
    turn(brain, "confirm forget this conversation")
    assert brain.memory.export("default")["exchanges"] == []
    turn(brain, "new topic")
    assert len(brain.memory.export("default")["exchanges"]) == 1
    assert all("saved discussion" not in json.dumps(p) for p in requests[-1:])


def test_forget_invalidates_all_clients_and_cascades_only_current_conversation(setup):
    brain, transport, requests = setup
    other_conversation = brain.memory.start_conversation("default")
    brain.memory.append(
        "default",
        other_conversation,
        Exchange("older", "human", "Older retained topic.", "Earlier reply."),
    )
    brain.memory.start_conversation("default")
    turn(brain, "FORGET_ME")
    target = brain.memory.forget_target("default")
    index = MemoryIndex(brain.memory)
    sources = index.sources("default", target[0], 0)
    note = Note(
        kind="topic", text="FORGET_ME", sources=[target[1]], scope="real", status="tentative"
    )
    assert index.save(
        "default", target[0], target[1], 0, sources[-1]["sequence"], Summary(notes=[note])
    )
    # Seed a derived vector without loading any model.
    with brain.memory.connect() as db:
        db.execute("INSERT INTO embedding_models VALUES ('test',1)")
        db.execute(
            "INSERT INTO embedding_vectors SELECT id,'test','hash',? "
            "FROM chunks WHERE conversation=?",
            (np.ones(1, dtype="<f4").tobytes(), target[0]),
        )
    turn(brain, "companion mode on", "laptop")
    assert brain.sessions["laptop"].history
    turn(brain, "forget this conversation")
    turn(brain, "confirm forget this conversation")
    assert brain.sessions["laptop"].history == []
    assert brain.sessions["laptop"].companion_mode
    exported = brain.memory.export("default")
    assert [row["id"] for row in exported["exchanges"]] == ["older"]
    for name in ("summaries", "notes", "summary_jobs"):
        assert all(row["conversation"] != target[0] for row in exported[name])
    with brain.memory.connect() as db:
        assert db.execute("SELECT count(*) FROM embedding_vectors").fetchone()[0] == 0
        assert (
            db.execute("SELECT count(*) FROM chunks WHERE conversation=?", (target[0],)).fetchone()[
                0
            ]
            == 0
        )
    assert not index.recall_evidence("default", target[0], (target[1],))
    turn(brain, "hello", "laptop")
    assert "FORGET_ME" not in json.dumps(requests[-1])


@pytest.mark.parametrize("change", ["expired", "another-client", "clear", "cancel", "private"])
def test_changed_or_cancelled_forgetting_never_deletes_new_data(setup, change):
    brain, transport, requests = setup
    turn(brain, "important saved topic")
    turn(brain, "forget this conversation")
    pending = brain.sessions["pi"].pending_forget
    if change == "expired":
        brain.sessions["pi"].pending_forget = replace(
            pending, expires_at=datetime.now(UTC) - timedelta(seconds=1)
        )
    elif change == "another-client":
        turn(brain, "a new saved exchange", "laptop")
    elif change == "clear":
        turn(brain, "clear context", "laptop")
    elif change == "cancel":
        turn(brain, "cancel forgetting")
    else:
        turn(brain, "private mode on")
    reply = turn(brain, "confirm forget this conversation")
    assert "deleted" not in reply.text or "No saved" in reply.text
    assert brain.memory.export("default")["exchanges"]


@pytest.mark.parametrize(
    "control", ["private mode on", "private mode off", "confirm forget this conversation"]
)
def test_control_synthesis_failure_does_not_commit_state_or_deletion(setup, control):
    brain, transport, requests = setup
    turn(brain, "saved")
    if control == "private mode off":
        turn(brain, "private mode on")
    if control.startswith("confirm"):
        turn(brain, "forget this conversation")
    before = brain.memory.policy("default")
    brain.stt.recognize.return_value = control
    brain.tts.synthesize.side_effect = RuntimeError("no speech")
    with pytest.raises(RuntimeError):
        brain.turn("pi", VOICE)
    assert brain.memory.policy("default") == before
    assert len(brain.memory.export("default")["exchanges"]) == 1


def test_private_storage_guard_and_stale_policy_commit_are_fail_closed(setup):
    brain, transport, requests = setup
    conversation = brain.memory.active_conversation("default")
    epoch = brain.memory.policy("default")[1]
    brain.memory.set_private("default", True)
    with pytest.raises(MemoryPolicyError):
        brain.memory.append(
            "default", conversation, Exchange("bad", "human", "private leak", "reply")
        )
    brain.memory.set_private("default", False)
    with pytest.raises(MemoryPolicyError):
        brain.memory.append(
            "default",
            conversation,
            Exchange("bad", "human", "stale leak", "reply"),
            expected_epoch=epoch,
        )
    assert brain.memory.export("default")["exchanges"] == []


def test_private_forgetting_clears_only_private_ram_and_keeps_saved_archive(setup):
    brain, transport, requests = setup
    turn(brain, "saved")
    turn(brain, "private mode on")
    turn(brain, "private memory")
    before = brain.memory.export("default")["exchanges"]
    turn(brain, "confirm forget this conversation")
    assert brain.sessions["pi"].history
    turn(brain, "forget this conversation")
    assert brain.sessions["pi"].history == []
    assert brain.private_mode and brain.memory.export("default")["exchanges"] == before


def test_memory_disabled_private_mode_creates_no_database(tmp_path):
    settings = Settings(
        memory_enabled=False, memory_database=tmp_path / "unused" / "archive.sqlite3"
    )
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": "reply"}}]}
            )
        )
    ) as client:
        brain = Brain(settings, client, Mock(), Mock())
        turn(brain, "private mode on")
        turn(brain, "only RAM")
        turn(brain, "forget this conversation")
        turn(brain, "private mode off")
    assert not settings.memory_database.parent.exists()


def test_cli_deletion_preserves_private_flag_and_notifies_other_live_brain(setup):
    brain, transport, requests = setup
    turn(brain, "saved")
    turn(brain, "private mode on")
    before = brain.memory.policy("default")
    brain.memory.delete("default", None)
    after = brain.memory.policy("default")
    assert after[0] and after[1] > before[1]
    turn(brain, "another private message")
    assert brain.private_mode
    assert brain.memory.export("default")["exchanges"] == []


def test_client_private_header_is_backward_compatible_and_sticky_when_missing():
    headers = [{"X-Iris-Private": "1"}, {}, {"X-Iris-Private": "0"}]

    def respond(request):
        return httpx.Response(
            200, headers=headers.pop(0), json=TurnReply(heard="input", text="reply").model_dump()
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as transport:
        client = DroidClient("http://127.0.0.1:8765", "test", transport)
        client.turn(b"test", text_only=True)
        assert client.private_mode
        client.turn(b"test", text_only=True)
        assert client.private_mode
        client.turn(b"test", text_only=True)
        assert not client.private_mode


@pytest.mark.parametrize("show", [False, True])
def test_private_client_hides_transcripts_and_never_saves_reply_audio(tmp_path, capsys, show):
    secret = "PRIVATE_TRANSCRIPT_9917"
    destination = tmp_path / "diagnostic.wav"

    def private_reply(client, *args, **kwargs):
        client.private_mode = True
        return TurnReply(
            heard=secret, text=secret, audio=base64.b64encode(VOICE).decode(), finished=True
        )

    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("iris_droid.client.record_turn", return_value=VOICE),
        patch("iris_droid.client.DroidClient.turn", private_reply),
        patch("iris_droid.client.play_wav"),
        patch("iris_droid.client.stop_playback"),
    ):
        args = [
            "client",
            "--activation",
            "continuous",
            "--no-sounds",
            "--save-reply",
            str(destination),
        ]
        assert iris.main(args + (["--show-private-text"] if show else [])) == 0
    assert (secret in capsys.readouterr().out) is show
    assert not destination.exists()


def test_real_http_private_state_header_and_safe_error_logging(setup, caplog):
    brain, transport, requests = setup
    with HTTPServer(("127.0.0.1", 0), handler_for(brain, "test")) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with httpx.Client(trust_env=False) as http:
                client = DroidClient(f"http://127.0.0.1:{server.server_port}", "test", http)
                reply = client.turn(b"private mode on", text_only=True)
                assert client.private_mode and "Private mode on" in reply.text
                session = client.headers["X-Iris-Session"]
                brain.sessions[session].backend.complete = Mock(
                    side_effect=ChatError("PRIVATE_ERROR_DETAILS")
                )
                with pytest.raises(httpx.HTTPStatusError):
                    client.turn(b"secret", text_only=True)
                assert "PRIVATE_ERROR_DETAILS" not in caplog.text
                client.turn(b"private mode off", text_only=True)
                assert not client.private_mode
        finally:
            server.shutdown()
            worker.join(timeout=5)


def test_private_change_failure_never_claims_success_or_discards_working_context(setup):
    brain, transport, requests = setup
    turn(brain, "saved context")
    old = brain.sessions["pi"]
    with patch.object(brain.memory, "set_private", side_effect=sqlite3.OperationalError("locked")):
        with pytest.raises(sqlite3.Error):
            turn(brain, "private mode on")
    assert not brain.private_mode
    assert brain.sessions["pi"] is old and old.history
    assert not brain.memory.policy("default")[0]


def test_confirm_forget_storage_failure_keeps_target_and_pending_request(setup):
    brain, transport, requests = setup
    turn(brain, "saved context")
    turn(brain, "forget this conversation")
    pending = brain.sessions["pi"].pending_forget
    with patch.object(
        brain.memory, "forget_current", side_effect=sqlite3.OperationalError("disk problem")
    ):
        with pytest.raises(sqlite3.Error):
            turn(brain, "confirm forget this conversation")
    assert brain.sessions["pi"].pending_forget == pending
    assert len(brain.memory.export("default")["exchanges"]) == 1


def test_stale_summary_and_vector_work_cannot_restore_a_forgotten_conversation(setup):
    brain, transport, requests = setup
    turn(brain, "FORGETTABLE_SOURCE")
    archive = brain.memory
    target = archive.forget_target("default")
    index = MemoryIndex(archive)
    source = index.sources("default", target[0], 0)[0]
    note = Note(
        kind="topic",
        text="FORGETTABLE_SOURCE",
        sources=[source["id"]],
        scope="real",
        status="tentative",
    )

    class Encoder:
        model_id = "stale-test-model"
        dimensions = 1

        def encode(self, texts):
            archive.forget_current("default", target[0], target[1])
            return np.ones((len(texts), 1), dtype=np.float32)

    semantic = SemanticMemory(archive, Encoder())
    semantic.index_batch("default")
    assert not index.save(
        "default", target[0], target[1], 0, source["sequence"], Summary(notes=[note])
    )
    index.rebuild("default")
    with archive.connect() as db:
        assert db.execute("SELECT count(*) FROM embedding_vectors").fetchone()[0] == 0
    assert archive.export("default")["exchanges"] == []
    assert not index.recall_sources("default", ["FORGETTABLE"], None, None)


def test_private_tool_calls_stay_live_but_are_not_traced_or_archived(setup, capsys):
    from iris_chat_engines.messages import FunctionCall, ToolCall

    brain, transport, requests = setup
    turn(brain, "private mode on")
    capsys.readouterr()
    chat = brain.sessions["pi"]
    chat.backend.complete = Mock(
        side_effect=[
            Message(
                role="assistant",
                reasoning_content="PRIVATE_REASONING",
                tool_calls=[
                    ToolCall(
                        id="clock",
                        type="function",
                        function=FunctionCall(name="get_local_time", arguments="{}"),
                    )
                ],
            ),
            Message(role="assistant", content="The current time."),
        ]
    )
    assert turn(brain, "What's the time?").text == "The current time."
    assert any(message.role == "tool" for message in chat.history)
    assert not chat.settings.trace and not chat.tools.settings.trace
    assert capsys.readouterr().out == ""
    assert brain.memory.export("default")["exchanges"] == []


def test_other_owner_policy_and_history_are_untouched(setup):
    brain, transport, requests = setup
    archive = brain.memory
    other = archive.active_conversation("other")
    archive.append(
        "other", other, Exchange("other-source", "human", "Other person's topic", "Other reply")
    )
    turn(brain, "private mode on")
    assert not archive.policy("other")[0]
    turn(brain, "private mode off")
    turn(brain, "saved topic")
    turn(brain, "forget this conversation")
    turn(brain, "confirm forget this conversation")
    assert archive.export("other")["exchanges"][0]["id"] == "other-source"


def test_private_metadata_migration_rolls_back_atomically(setup):
    brain, transport, requests = setup
    turn(brain, "saved")
    with brain.memory.connect() as db:
        db.execute("DROP TABLE memory_policy")
        db.execute("CREATE TABLE memory_policy (conflict TEXT)")
        db.execute("PRAGMA user_version=3")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        SQLiteMemory(brain.memory.path)
    with brain.memory.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT count(*) FROM exchanges").fetchone()[0] == 1


def test_recalled_older_topic_is_not_silently_the_current_deletion_target(setup):
    from iris_memory.recall import Candidate, PendingRecall

    brain, transport, requests = setup
    turn(brain, "old topic")
    old = brain.memory.forget_target("default")
    turn(brain, "clear context")
    turn(brain, "new topic")
    brain.sessions["pi"].pending_recall = PendingRecall(
        brain.memory.forget_target("default")[0],
        (Candidate(old[0], "2026-11-01", "older recalled topic", (old[1],), 1),),
        datetime.now(UTC) + timedelta(minutes=5),
        0,
    )
    reply = turn(brain, "forget this conversation")
    assert "older saved conversation" in reply.text
    assert brain.sessions["pi"].pending_forget is None
    turn(brain, "forget the current conversation")
    turn(brain, "confirm forget this conversation")
    assert len(brain.memory.export("default")["exchanges"]) == 1
    assert brain.memory.export("default")["exchanges"][0]["user_text"] == "old topic"


def test_policy_change_during_confirmation_never_deletes_the_target(setup):
    brain, transport, requests = setup
    turn(brain, "saved topic")
    turn(brain, "forget this conversation")
    brain.stt.recognize.return_value = "confirm forget this conversation"

    def changed_policy(text):
        brain.memory.set_private("default", True)
        brain.memory.set_private("default", False)
        return np.zeros(10, dtype=np.float32), 24000

    brain.tts.synthesize.side_effect = changed_policy
    with pytest.raises(MemoryPolicyError):
        brain.turn("pi", VOICE)
    assert len(brain.memory.export("default")["exchanges"]) == 1
