"""Grounding, compaction, recovery and retrieval over temporary source archives."""

import json
import sqlite3
from unittest.mock import Mock, patch

import httpx
import numpy as np
import pytest
from pydantic import ValidationError

import iris
from iris_chat_engines.budget import estimate, fit, input_budget
from iris_chat_engines.compatible import ChatEngine
from iris_chat_engines.messages import ChatError, FunctionCall, Message, ToolCall
from iris_droid.protocol import write_wav
from iris_droid.server import Brain
from iris_memory import Exchange, SQLiteMemory
from iris_memory.context import MemoryContext
from iris_memory.index import MemoryIndex, migrate
from iris_memory.models import Note, Summary
from iris_settings import Settings


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        memory_enabled=True,
        memory_database=tmp_path / "archive.sqlite3",
        memory_summary_interval=2,
        tools_enabled=False,
    )
    archive = SQLiteMemory(settings.memory_database)
    conversation = archive.active_conversation("default")
    return settings, archive, MemoryIndex(archive), conversation


def append(archive, conversation, number, text=None, *, owner="default", origin="human"):
    exchange = Exchange(
        str(number), origin, text or f"Project robot detail {number}.", "Iris suggests a hat."
    )
    archive.append(owner, conversation, exchange)
    return exchange


def note(source, **kwargs):
    return Note(
        kind="user_assertion",
        text="The robot is blue.",
        sources=[str(source)],
        scope="fictional",
        status="tentative",
        **kwargs,
    )


class SummaryBackend:
    def __init__(self):
        self.requests = []
        self.change = None

    def complete(self, messages, tools):
        assert not tools
        self.requests.append(messages)
        payload = json.loads(messages[-1].content)
        first = payload["original_exchanges"][0]
        result = Summary(notes=[note(first["id"])])
        if self.change:
            return self.change(payload, result)
        return Message(role="assistant", content=result.model_dump_json())


def test_summaries_keep_sources_restart_and_originals(setup):
    settings, archive, index, conversation = setup
    append(archive, conversation, 1, "Our fictional robot is blue.")
    backend = SummaryBackend()
    memory = MemoryContext(settings, index, backend)
    assert not memory.checkpoint("default", conversation)
    append(archive, conversation, 2)
    assert memory.checkpoint("default", conversation)
    assert index.pending("default", conversation) is None
    saved = index.notes("default", conversation)
    assert saved[0]["scope"] == "fictional" and json.loads(saved[0]["sources"]) == ["1"]
    append(archive, conversation, 3)
    append(archive, conversation, 4)
    assert memory.checkpoint("default", conversation)
    payload = json.loads(backend.requests[-1][-1].content)
    assert [row["id"] for row in payload["original_exchanges"]] == ["3", "4"]
    assert payload["earlier_notes"][0]["id"] == saved[0]["id"]
    reopened = SQLiteMemory(settings.memory_database)
    assert len(reopened.export("default")["summaries"]) == 2
    assert len(reopened.export("default")["exchanges"]) == 4
    assert len(MemoryIndex(reopened).notes("default", conversation)) == 2


@pytest.mark.parametrize(
    "failure", ["malformed", "invented_source", "invented_prior", "tool", "timeout", "automatic"]
)
def test_failed_summary_retains_previous_valid_version_and_retries(setup, failure, caplog):
    settings, archive, index, conversation = setup
    append(archive, conversation, 1)
    backend = SummaryBackend()
    memory = MemoryContext(settings, index, backend)
    assert memory.checkpoint("default", conversation, force=True)
    before = index.notes("default", conversation)
    append(archive, conversation, 2, origin="automatic" if failure == "automatic" else "human")

    def invalid(payload, result):
        if failure == "timeout":
            raise ChatError("private source diagnostic")
        if failure == "malformed":
            return Message(role="assistant", content="private malformed source")
        if failure == "invented_source":
            result.notes[0].sources = ["not-a-source"]
        if failure == "invented_prior":
            result.notes[0].supersedes = ["another-owner-note"]
        if failure == "tool":
            return Message(
                role="assistant",
                content="private",
                tool_calls=[
                    ToolCall(
                        id="x",
                        type="function",
                        function=FunctionCall(name="get_local_time", arguments="{}"),
                    )
                ],
            )
        return Message(role="assistant", content=result.model_dump_json())

    backend.change = invalid
    assert not memory.checkpoint("default", conversation, force=True)
    assert index.notes("default", conversation) == before
    assert "private source" not in caplog.text and "private malformed" not in caplog.text
    assert index.pending("default", conversation) is None  # backed off, not lost
    assert archive.export("default")["summary_jobs"][0]["attempts"] == 1
    reopened = MemoryIndex(SQLiteMemory(settings.memory_database))
    reopened.retry("default")
    backend.change = None
    if failure != "automatic":
        assert memory.checkpoint("default", conversation, force=True)


def test_stale_or_deleted_job_cannot_resurrect_or_overwrite(setup):
    settings, archive, index, conversation = setup
    append(archive, conversation, 1)
    backend = SummaryBackend()
    memory = MemoryContext(settings, index, backend)

    def concurrent_write(payload, result):
        append(archive, conversation, 2, "Correction: the robot is green.")
        return Message(role="assistant", content=result.model_dump_json())

    backend.change = concurrent_write
    assert not memory.checkpoint("default", conversation, force=True)
    assert index.notes("default", conversation) == []

    def deletion(payload, result):
        archive.delete("default", conversation)
        return Message(role="assistant", content=result.model_dump_json())

    backend.change = deletion
    assert not memory.checkpoint("default", conversation, force=True)
    exported = archive.export("default")
    assert not exported["exchanges"] and not exported["summaries"] and not exported["summary_jobs"]


def test_corrections_supersede_notes_and_old_search_chunks(setup):
    settings, archive, index, conversation = setup
    append(archive, conversation, 1, "The robot's name is Sapphire.")
    backend = SummaryBackend()
    memory = MemoryContext(settings, index, backend)
    assert memory.checkpoint("default", conversation, force=True)
    previous = index.notes("default", conversation)[0]
    append(archive, conversation, 2, "Correction: the robot's name is Emerald.")

    def correction(payload, result):
        updated = note("2", supersedes=[previous["id"]])
        updated.text = "The robot is now named Emerald."
        return Message(role="assistant", content=Summary(notes=[updated]).model_dump_json())

    backend.change = correction
    assert memory.checkpoint("default", conversation, force=True)
    assert len(index.notes("default", conversation)) == 1
    assert "Emerald" in index.notes("default", conversation)[0]["text"]
    assert not index.search("default", conversation, "Sapphire", "")
    assert index.search("default", conversation, "Emerald", "")[0]["exchange"] == "2"
    assert len(archive.export("default")["exchanges"]) == 2


def test_search_context_dates_owner_escaping_clear_and_delete(setup):
    settings, archive, index, conversation = setup
    append(archive, conversation, 1, "Sapphire robot has a garden project.")
    outsider = archive.active_conversation("other")
    append(archive, outsider, 2, "Sapphire private family details.", owner="other")
    found = index.search("default", conversation, "What about its garden?", "Sapphire robot")
    assert found and found[0]["exchange"] == "1" and "+00:00" in found[0]["created_at"]
    assert all(row["owner"] == "default" for row in found)
    assert index.search("default", conversation, 'Sapphire OR NOT " ** ()', "")
    fresh = archive.start_conversation("default")
    assert not index.search("default", fresh, "Sapphire", "")
    assert index.search("default", fresh, "Remember Sapphire?", "")
    archive.delete("default", conversation)
    assert not index.search("default", fresh, "Remember Sapphire?", "")
    with archive.connect() as db:
        assert db.execute("SELECT count(*) FROM chunks WHERE exchange='1'").fetchone()[0] == 0
        db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('integrity-check')")


def test_migration_indexes_phase_one_sources_atomically(tmp_path):
    path = tmp_path / "v1.sqlite3"
    archive = SQLiteMemory(path)
    conversation = archive.active_conversation("default")
    append(archive, conversation, 1, "Sapphire archive before migration.")
    # Strip derived tables to recreate the phase-one schema around its source data.
    with archive.connect() as db:
        for table in (
            "detail_supersessions",
            "memory_attention",
            "memory_topics",
            "memory_facts",
            "conversation_uses",
            "detail_uses",
            "memory_details",
            "companion_topics",
            "memory_policy",
            "embedding_vectors",
            "embedding_models",
            "notes",
            "summaries",
            "summary_jobs",
            "recall_blocks",
            "chunks",
            "chunks_fts",
        ):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=1")
    migrated = SQLiteMemory(path)
    assert MemoryIndex(migrated).search("default", conversation, "Sapphire", "")
    assert len(migrated.export("default")["exchanges"]) == 1
    with migrated.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 6
    assert len(MemoryIndex(SQLiteMemory(path)).search("default", conversation, "Sapphire", "")) == 1


def test_fts_unavailable_has_actionable_error():
    with pytest.raises(ValueError, match="Install Python with FTS5"):
        migrate(Mock(execute=Mock(side_effect=sqlite3.OperationalError("no such module: fts5"))))


def test_queue_is_bounded_and_jobs_cascade_with_owner_deletion(setup):
    settings, archive, index, conversation = setup
    for number in range(66):
        conversation = archive.start_conversation("default")
        append(archive, conversation, number)
    assert len(archive.export("default")["summary_jobs"]) == 64
    archive.delete("default", None)
    exported = archive.export("default")
    assert not exported["summary_jobs"] and not exported["summaries"] and not exported["notes"]


def test_checkpoint_input_and_output_budgets_are_separate_and_bounded(setup):
    settings, archive, index, conversation = setup
    settings = settings.model_copy(
        update={"model_context_tokens": 4096, "memory_summary_tokens": 600}
    )
    for number in range(12):
        append(archive, conversation, number, "Long project details. " * 20)
    backend = SummaryBackend()
    memory = MemoryContext(settings, index, backend)
    assert memory.checkpoint("default", conversation, force=True)
    assert (
        estimate(backend.requests[0], [])
        + settings.memory_summary_tokens
        + settings.context_margin_tokens
        <= settings.model_context_tokens
    )
    originals = json.loads(backend.requests[0][-1].content)["original_exchanges"]
    assert 0 < len(originals) < 12
    assert index.pending("default", conversation) is not None


def test_context_compaction_keeps_summary_and_recent_whole_pairs(setup):
    settings, archive, index, conversation = setup
    settings = settings.model_copy(update={"model_context_tokens": 6000, "history_turns": 50})
    history = []
    for number in range(10):
        exchange = append(
            archive, conversation, number, f"Sapphire project step {number}. " + "x" * 350
        )
        history += [
            Message(role="user", content=exchange.user_text),
            Message(role="assistant", content=exchange.assistant_text),
        ]
    backend = SummaryBackend()
    memory = MemoryContext(settings, index, backend)
    reference = memory.prepare(
        "default", conversation, "What comes next for Sapphire?", history, "Friendly Iris.", []
    )
    assert "The robot is blue." in reference
    messages = fit(
        settings,
        "Friendly Iris.",
        history,
        Message(role="user", content="What comes next?"),
        [],
        reference,
    )
    assert estimate(messages, []) <= input_budget(settings)
    assert [m.role for m in messages] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert "step 8" in messages[1].content and "step 9" in messages[3].content
    assert len(archive.export("default")["exchanges"]) == 10


def test_unfit_single_input_fails_without_model_call_or_history_mutation():
    backend = Mock()
    settings = Settings(model_context_tokens=4096, tools_enabled=False)
    with httpx.Client() as client:
        chat = ChatEngine(settings, client, backend=backend)
        with pytest.raises(ChatError, match="exceeds model_context_tokens"):
            chat.get_response("x" * 8000)
        backend.complete.assert_not_called()
        assert chat.history == []


def test_tool_overflow_never_sends_unpaired_or_oversized_followup():
    backend = Mock(
        complete=Mock(
            return_value=Message(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="clock",
                        type="function",
                        function=FunctionCall(name="get_local_time", arguments="{}"),
                    )
                ],
            )
        )
    )
    with httpx.Client() as client:
        chat = ChatEngine(Settings(model_context_tokens=4096), client, backend=backend)
        with patch.object(chat.tools, "execute", return_value="x" * 10000):
            with pytest.raises(ChatError, match="tool exchange exceeds"):
                chat.get_response("What time?")
        assert backend.complete.call_count == 1 and chat.history == []


def test_derived_search_failure_falls_back_to_summary(setup):
    settings, archive, index, conversation = setup
    append(archive, conversation, 1)
    memory = MemoryContext(settings, index, SummaryBackend())
    assert memory.checkpoint("default", conversation, force=True)
    with patch.object(index, "search", side_effect=sqlite3.OperationalError("index failed")):
        reference = memory.prepare("default", conversation, "Sapphire", [], "Iris", [])
    assert "The robot is blue." in reference


def test_retry_cli_and_export_include_derived_data(setup, tmp_path, capsys):
    settings, archive, index, conversation = setup
    append(archive, conversation, 1)
    index.failed("default", conversation)
    config = tmp_path / "config.json"
    config.write_text(settings.model_dump_json())
    assert iris.main(["memory", "--config", str(config), "retry"]) == 0
    assert index.pending("default", conversation)["attempts"] == 0
    memory = MemoryContext(settings, index, SummaryBackend())
    assert memory.checkpoint("default", conversation, force=True)
    capsys.readouterr()
    assert iris.main(["memory", "--config", str(config), "export"]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["summaries"] and exported["notes"] and exported["schema_version"] == 6


def test_budget_settings_and_environment_validation(monkeypatch):
    for kwargs in (
        {"model_context_tokens": 200},
        {"model_context_tokens": 4096, "max_tokens": 4000},
        {"memory_summary_interval": 0},
        {"memory_summary_chars": -1},
    ):
        with pytest.raises(ValidationError):
            Settings(**kwargs)
    monkeypatch.setenv("IRIS_MODEL_CONTEXT_TOKENS", "16384")
    assert Settings().model_context_tokens == 16384


def test_one_minute_client_default_and_server_accepts_45_seconds():
    with (
        patch.dict("os.environ", {"IRIS_DROID_TOKEN": "test"}),
        patch("iris_droid.client.record_turn", side_effect=KeyboardInterrupt) as record,
    ):
        assert iris.main(["client", "--activation", "continuous", "--no-sounds"]) == 0
        assert record.call_args.args[1] == 60
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"message": {"role": "assistant", "content": "Hello"}}]}
            )
        )
    )
    with client:
        brain = Brain(
            Settings(),
            client,
            Mock(recognize=Mock(return_value="long thought")),
            Mock(synthesize=Mock(return_value=(np.zeros(10, dtype=np.float32), 24000))),
        )
        reply = brain.turn("pi", write_wav(b"\x00\x00" * 16000 * 45, 16000))
        assert reply.heard == "long thought"


def test_long_single_exchange_uses_labelled_excerpts_without_blocking_queue(setup):
    settings, archive, index, conversation = setup
    settings = settings.model_copy(
        update={"model_context_tokens": 4096, "memory_summary_tokens": 600}
    )
    append(archive, conversation, 1, "Sapphire " * 1200)
    backend = SummaryBackend()
    memory = MemoryContext(settings, index, backend)
    assert memory.checkpoint("default", conversation, force=True)
    payload = json.loads(backend.requests[0][-1].content)
    assert payload["original_exchanges"][0]["excerpted"]
    assert "middle omitted" in payload["original_exchanges"][0]["user_text"]
    assert index.notes("default", conversation)[0]["status"] == "tentative"
    assert len(archive.export("default")["exchanges"][0]["user_text"]) == 10800
    append(archive, conversation, 2, "A later project decision.")
    assert memory.checkpoint("default", conversation, force=True)


def test_closed_conversation_backlog_resumes_one_job_per_turn(setup):
    settings, archive, index, old = setup
    for number in range(15):
        append(archive, old, number)
    fresh = archive.start_conversation("default")
    backend = SummaryBackend()
    memory = MemoryContext(settings, index, backend)
    memory.prepare("default", fresh, "new topic", [], "Iris", [])
    assert len(backend.requests) == 1 and index.notes("default", old)
    assert index.pending("default", old) is not None
    memory.prepare("default", fresh, "new topic", [], "Iris", [])
    assert len(backend.requests) == 2


def test_server_summaries_survive_restart_and_ground_a_fresh_prompt(setup):
    settings, archive, index, conversation = setup
    requests = []

    def complete(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["messages"][0]["content"].startswith("Extract memory metadata"):
            assert payload["max_tokens"] == settings.memory_summary_tokens
            message = {"role": "assistant", "content": '{"facts": [], "topics": []}'}
        elif payload["messages"][0]["content"].startswith("Summarise these original"):
            data = json.loads(payload["messages"][-1]["content"])
            result = Summary(notes=[note(data["original_exchanges"][0]["id"])])
            assert payload["max_tokens"] == settings.memory_summary_tokens
            message = {"role": "assistant", "content": result.model_dump_json()}
        else:
            assert payload["max_tokens"] == settings.max_tokens
            message = {"role": "assistant", "content": "We can carry on with the robot."}
        assert (
            estimate(
                [Message.model_validate(m) for m in payload["messages"]], payload.get("tools", [])
            )
            <= settings.model_context_tokens
            - payload["max_tokens"]
            - settings.context_margin_tokens
        )
        return httpx.Response(200, json={"choices": [{"message": message}]})

    with httpx.Client(transport=httpx.MockTransport(complete)) as client:
        brain = Brain(settings, client, Mock(), Mock())
        for text in (b"Our robot is blue.", b"It wears a hat.", b"What's next?"):
            brain.turn("pi", text, text_only=True)
        assert any("active_conversation_notes" in p["messages"][0]["content"] for p in requests)
        brain = Brain(settings, client, Mock(), Mock())
        brain.turn("new-client", b"Let's carry on with the robot.", text_only=True)
        assert "The robot is blue." in requests[-1]["messages"][0]["content"]
        brain.turn("new-client", b"clear context", text_only=True)
        brain.turn("new-client", b"unrelated greeting", text_only=True)
        assert "The robot is blue." not in requests[-1]["messages"][0]["content"]
        brain.turn("new-client", b"Remember the blue robot?", text_only=True)
        assert "source_excerpts" in requests[-1]["messages"][0]["content"]


def test_rebuild_preserves_sources_and_owner_isolation(setup):
    settings, archive, index, conversation = setup
    append(archive, conversation, 1, "Sapphire project.")
    memory = MemoryContext(settings, index, SummaryBackend())
    assert memory.checkpoint("default", conversation, force=True)
    other = archive.active_conversation("other")
    append(archive, other, 2, owner="other")
    assert memory.checkpoint("other", other, force=True)
    source = archive.export("default")["exchanges"]
    index.rebuild("default")
    assert archive.export("default")["exchanges"] == source
    assert not archive.export("default")["summaries"]
    assert archive.export("other")["summaries"]
    assert index.pending("default", conversation)
    assert index.search("default", conversation, "Sapphire", "")


def test_second_migration_failure_keeps_phase_one_archive(setup):
    settings, archive, index, conversation = setup
    append(archive, conversation, 1)
    with archive.connect() as db:
        for table in (
            "detail_supersessions",
            "memory_attention",
            "memory_topics",
            "memory_facts",
            "conversation_uses",
            "detail_uses",
            "memory_details",
            "companion_topics",
            "memory_policy",
            "embedding_vectors",
            "embedding_models",
            "notes",
            "summaries",
            "summary_jobs",
            "recall_blocks",
            "chunks",
            "chunks_fts",
        ):
            db.execute(f"DROP TABLE {table}")
        db.execute("PRAGMA user_version=1")
        db.execute("CREATE TABLE notes (unrelated TEXT)")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        SQLiteMemory(settings.memory_database)
    with archive.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='summaries'").fetchone() is None
        )
        assert db.execute("SELECT count(*) FROM exchanges").fetchone()[0] == 1


def test_summary_interval_can_exceed_one_bounded_batch(setup):
    settings, archive, index, conversation = setup
    settings = settings.model_copy(update={"memory_summary_interval": 15})
    for number in range(15):
        append(archive, conversation, number)
    backend = SummaryBackend()
    assert MemoryContext(settings, index, backend).checkpoint("default", conversation)
    assert len(json.loads(backend.requests[0][-1].content)["original_exchanges"]) <= 12
    assert index.pending("default", conversation)["pending_count"] > 0
