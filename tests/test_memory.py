"""Persistence and server lifecycle checks with no real inference or audio devices."""

import json
import sqlite3
import threading
from contextlib import chdir
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import httpx
import numpy as np
import pytest

import configure
import iris
from iris_droid.protocol import write_wav
from iris_droid.server import Brain, handler_for
from iris_memory import (
    AUTOMATIC_EVENT,
    Exchange,
    SQLiteMemory,
    database_path,
    default_database_path,
)
from iris_memory.index import MemoryIndex
from iris_memory.models import Note, Summary
from iris_settings import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(memory_enabled=True, memory_database=tmp_path / "data" / "memory.sqlite3")


def make_brain(settings, requests=None, *, tool=False):
    requests = requests if requests is not None else []

    def complete(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if tool and payload["messages"][-1]["role"] == "user":
            message = {
                "role": "assistant",
                "content": None,
                "reasoning_content": "private thought",
                "tool_calls": [
                    {
                        "id": "clock",
                        "type": "function",
                        "function": {"name": "get_local_time", "arguments": "{}"},
                    }
                ],
            }
        else:
            message = {"role": "assistant", "content": "Let's continue our conversation."}
        return httpx.Response(200, json={"choices": [{"message": message}]})

    client = httpx.Client(transport=httpx.MockTransport(complete))
    return Brain(
        settings,
        client,
        Mock(recognize=Mock(return_value="voice input")),
        Mock(synthesize=Mock(return_value=(np.zeros(100, dtype=np.float32), 24000))),
    ), client


def store(settings):
    return SQLiteMemory(database_path(settings), create=False)


def exchanges(settings):
    return store(settings).export(settings.memory_owner)["exchanges"]


def test_delete_previous_removes_source_and_summary_and_refreshes_all_clients(settings):
    settings = settings.model_copy(update={"memory_hierarchy_enabled": False})
    requests = []
    brain, client = make_brain(settings, requests)
    with client:
        saved = brain.turn("phone", b"Kept earlier sentence", text_only=True)
        wrong = brain.turn("phone", b"Accidental marmalade", text_only=True)
        archive = brain.memory
        owner = settings.memory_owner
        conversation = archive.current_conversation(owner)
        index = MemoryIndex(archive)
        with archive.connect() as db:
            sequence = db.execute("SELECT max(sequence) FROM exchanges").fetchone()[0]
        note = Note(
            text="Accidental marmalade",
            kind="user_assertion",
            scope="real",
            status="confirmed",
            sources=[wrong._exchange_id],
        )
        assert index.save(
            owner, conversation, wrong._exchange_id, 0, sequence, Summary(notes=[note])
        )

        deleted = brain.turn("pi", b" DELETE   PREVIOUS! ", text_only=True)
        assert deleted._deleted_exchange_id == wrong._exchange_id
        assert brain.memory.current_conversation(owner) == conversation
        exported = archive.export(owner)
        assert [row["id"] for row in exported["exchanges"]] == [saved._exchange_id]
        assert not exported["summaries"] and not exported["notes"]
        assert index.pending(owner, conversation) is not None
        assert index.search(owner, conversation, "marmalade", "") == []

        brain.turn("phone", b"Retry this message", text_only=True)
        prompt = requests[-1]["messages"]
        assert [row["content"] for row in prompt if row["role"] == "user"] == [
            "Kept earlier sentence",
            "Retry this message",
        ]


@pytest.mark.parametrize("private", [True, False])
def test_delete_previous_live_history_is_limited_to_issuing_session(settings, private):
    if not private:
        settings = settings.model_copy(update={"memory_enabled": False})
    brain, client = make_brain(settings)
    with client:
        if private:
            brain.turn("pi", b"Previously saved", text_only=True)
            brain.turn("pi", b"private mode on", text_only=True)
        brain.turn("pi", b"First live turn", text_only=True)
        other = brain.turn("phone", b"Another client's turn", text_only=True)
        brain.turn("pi", b"Please describe the words delete previous", text_only=True)
        brain.stt.recognize.return_value = "Delete previous."
        deleted = brain.turn("pi", write_wav(b"\0\0" * 160, 16000))
        assert deleted.text == "Previous exchange deleted." and deleted.audio
        assert [m.content for m in brain.sessions["pi"].history if m.role == "user"] == [
            "First live turn"
        ]
        assert brain.sessions["phone"].history[0]._exchange_id == other._exchange_id
        brain.turn("pi", b"delete previous", text_only=True)
        empty = brain.turn("pi", b"delete previous", text_only=True)
        assert "no previous exchange" in empty.text and not empty.audio
        if private:
            assert [exchange["user_text"] for exchange in exchanges(settings)] == [
                "Previously saved"
            ]


def test_schema_idempotency_order_ownership_and_deletion(settings):
    memory = SQLiteMemory(database_path(settings))
    conversation = memory.active_conversation("dave")
    first = Exchange(str(uuid4()), "human", "story", "reply", ("get_local_time",))
    second = Exchange(str(uuid4()), "automatic", AUTOMATIC_EVENT, "opener")
    memory.append("dave", conversation, first)
    memory.append("dave", conversation, first)
    memory.append("dave", conversation, second)
    assert memory.recent("dave", conversation, 10, 1000) == [first, second]
    assert memory.recent("other", conversation, 10, 1000) == []
    with pytest.raises(ValueError, match="already belongs"):
        memory.append("other", conversation, first)
    with pytest.raises(sqlite3.IntegrityError):
        memory.append("other", conversation, Exchange(str(uuid4()), "human", "no", "no"))
    with pytest.raises(ValueError, match="No matching"):
        memory.delete("other", conversation)
    assert memory.conversations("dave")[0]["turns"] == 2
    with memory.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    memory.delete("dave", conversation)
    assert memory.export("dave")["exchanges"] == []
    assert memory.active_conversation("dave") != conversation


def test_whole_exchange_character_and_count_limits(settings):
    memory = SQLiteMemory(database_path(settings))
    conversation = memory.active_conversation("default")
    for i in range(4):
        memory.append("default", conversation, Exchange(str(i), "human", str(i) * 10, "x" * 10))
    assert [e.id for e in memory.recent("default", conversation, 2, 100)] == ["2", "3"]
    assert [e.id for e in memory.recent("default", conversation, 10, 40)] == ["2", "3"]
    assert memory.recent("default", conversation, 10, 19) == []
    assert memory.recent("default", conversation, 0, 100) == []


def test_restart_client_server_exit_eviction_and_shared_owner(settings):
    # Test full RAM restoration independently of context-pressure compaction.
    settings = settings.model_copy(update={"model_context_tokens": 16384})
    requests = []
    brain, client = make_brain(settings, requests)
    with client:
        brain.turn("pi", b"Our story has a blue robot.", text_only=True)
        brain.turn("pi", b"exit", text_only=True)
        assert "pi" not in brain.sessions
    brain, client = make_brain(settings, requests)
    with client:
        # A different transport UUID resumes the server's explicit owner conversation.
        brain.turn("new-pi", b"What happens next?", text_only=True)
        assert requests[-1]["messages"][1]["content"] == "Our story has a blue robot."
        assert not brain.sessions["new-pi"].companion_mode
        brain.turn("laptop", b"Let's give it a hat.", text_only=True)
        brain.turn("new-pi", b"Which hat?", text_only=True)
        assert any(m["content"] == "Let's give it a hat." for m in requests[-1]["messages"])
        for i in range(16):
            brain.turn(str(i), b"hello", text_only=True)
        assert "new-pi" not in brain.sessions
        brain.turn("new-pi", b"back again", text_only=True)
        assert len(brain.sessions["new-pi"].history) == settings.history_turns * 2
    assert len(exchanges(settings)) == 21


def test_clear_context_rotates_shared_conversation_without_deleting_archive(settings):
    requests = []
    brain, client = make_brain(settings, requests)
    with client:
        brain.turn("pi", b"old topic", text_only=True)
        brain.turn("laptop", b"companion mode on", text_only=True)
        reply = brain.turn("laptop", b"clear context", text_only=True)
        assert reply.companion_mode
        brain.turn("pi", b"new topic", text_only=True)
        assert [m["role"] for m in requests[-1]["messages"]] == ["system", "user"]
    assert len(store(settings).conversations("default")) == 2
    assert len(exchanges(settings)) == 2
    brain, client = make_brain(settings, requests)
    with client:
        brain.turn("new", b"carry on", text_only=True)
        assert requests[-1]["messages"][1]["content"] == "new topic"
        assert all(m["content"] != "old topic" for m in requests[-1]["messages"])


def test_stop_listening_preserves_memory_and_automatic_origin(settings):
    brain, client = make_brain(settings)
    with client:
        brain.turn("pi", b"companion mode on", text_only=True)
        brain.turn("pi", b"idle", text_only=True, companion_idle=True)
        brain.turn("pi", b"stop listening", text_only=True)
        assert not brain.sessions["pi"].companion_mode
    saved = exchanges(settings)
    assert len(saved) == 1 and saved[0]["origin"] == "automatic"
    assert saved[0]["user_text"] == AUTOMATIC_EVENT
    brain, client = make_brain(settings)
    with client:
        reply = brain.turn("new", b"idle", text_only=True, companion_idle=True)
        assert not reply.companion_mode and "new" not in brain.sessions
        brain.turn("new", b"hello", text_only=True)
        assert brain.sessions["new"].history[0].content == AUTOMATIC_EVENT


@pytest.mark.parametrize("failure", ["model", "tts", "oversized", "storage"])
def test_failed_turns_never_commit_and_roll_back(settings, failure):
    brain, client = make_brain(settings)
    with client:
        brain.turn("pi", b"successful", text_only=True)
        previous = brain.sessions["pi"].history.copy()
        if failure == "model":
            brain.sessions["pi"].backend.complete = Mock(side_effect=RuntimeError("model failed"))
        elif failure == "tts":
            brain.tts.synthesize.side_effect = RuntimeError("synthesis failed")
        elif failure == "storage":
            brain.memory.append = Mock(side_effect=sqlite3.OperationalError("disk full"))
        with patch("iris_app.replies.MAX_BODY", 1 if failure == "oversized" else 99999999):
            with pytest.raises((RuntimeError, ValueError, sqlite3.Error)):
                brain.turn("pi", b"failed", text_only=failure != "tts")
        assert brain.sessions["pi"].history == previous
    assert len(exchanges(settings)) == 1


def test_tools_live_history_and_safe_restoration(settings):
    requests = []
    settings = Settings(
        **{**settings.model_dump(), "memory_hierarchy_enabled": False}, api_key="archive-secret"
    )
    brain, client = make_brain(settings, requests, tool=True)
    with client:
        brain.turn("pi", b"What time is it?", text_only=True)
        brain.turn("pi", b"And now?", text_only=True)
        assert any(m.get("reasoning_content") == "private thought" for m in requests[2]["messages"])
    raw = database_path(settings).read_bytes()
    assert b"private thought" not in raw and b"tool_call_id" not in raw
    assert b"archive-secret" not in raw
    assert exchanges(settings)[0]["tool_names"] == ["get_local_time"]
    requests.clear()
    brain, client = make_brain(settings, requests)
    with client, patch("iris_tools.ToolRegistry.execute") as execute:
        brain.turn("new", b"carry on", text_only=True)
        execute.assert_not_called()
    assert [m["role"] for m in requests[0]["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert all(
        "tool_calls" not in m and "reasoning_content" not in m for m in requests[0]["messages"]
    )


def test_zero_history_still_archives_but_never_restores(settings):
    settings = settings.model_copy(update={"history_turns": 0})
    brain, client = make_brain(settings)
    with client:
        brain.turn("pi", b"first", text_only=True)
    requests = []
    brain, client = make_brain(settings, requests)
    with client:
        brain.turn("new", b"second", text_only=True)
        assert len(requests[0]["messages"]) == 2
        assert brain.sessions["new"].history == []
    assert len(exchanges(settings)) == 2


def test_disabled_memory_never_creates_directory(settings):
    settings = settings.model_copy(update={"memory_enabled": False})
    brain, client = make_brain(settings)
    with client:
        brain.turn("pi", b"hello", text_only=True)
        brain.turn("new", b"fresh", text_only=True)
        assert len(brain.sessions["new"].history) == 2
    assert brain.memory is None and not database_path(settings).parent.exists()


def test_newer_schema_missing_and_unwritable_archives_fail_explicitly(settings, tmp_path):
    with pytest.raises(ValueError, match="No memory archive"):
        SQLiteMemory(database_path(settings), create=False)
    assert not database_path(settings).parent.exists()
    memory = SQLiteMemory(database_path(settings))
    with memory.connect() as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="Unsupported"):
        SQLiteMemory(database_path(settings))
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    with pytest.raises(OSError):
        SQLiteMemory(blocked / "archive.sqlite3")


def test_locked_write_is_atomic_and_recovers(settings):
    memory = SQLiteMemory(database_path(settings))
    conversation = memory.active_conversation("default")
    exchange = Exchange("stable", "human", "hello", "reply")
    with memory.connect() as lock:
        lock.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            memory.append("default", conversation, exchange)
    assert memory.export("default")["exchanges"] == []
    memory.append("default", conversation, exchange)
    assert len(memory.export("default")["exchanges"]) == 1


def test_cli_list_export_delete_and_missing_archive(settings, tmp_path, capsys):
    config = tmp_path / "settings.json"
    config.write_text(settings.model_dump_json())
    command = ["memory", "--config", str(config)]
    assert iris.main([*command, "list"]) == 1
    assert not database_path(settings).exists()
    brain, client = make_brain(settings)
    with client:
        brain.turn("pi", b"test archive", text_only=True)
    capsys.readouterr()
    assert iris.main([*command, "export"]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["exchanges"][0]["user_text"] == "test archive"
    assert iris.main([*command, "list"]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed[0]["turns"] == 1
    assert iris.main([*command, "delete", "--conversation", listed[0]["id"]]) == 0
    assert exchanges(settings) == []
    assert iris.main([*command, "delete", "--all"]) == 0
    with pytest.raises(SystemExit):
        iris.main([*command, "delete"])


def test_wizard_memory_opt_in(tmp_path):
    def answer(prompt):
        if prompt.startswith("Save conversation memory"):
            return "yes"
        if prompt.startswith("Memory database"):
            return str(tmp_path / "archive.sqlite3")
        if prompt.startswith("Memory owner"):
            return "dave"
        return ""

    with chdir(tmp_path), patch("builtins.input", side_effect=answer):
        assert configure.main([]) == 0
    saved = json.loads((tmp_path / "local-config.json").read_text())
    assert saved["memory_enabled"] and saved["memory_owner"] == "dave"
    assert saved["memory_database"] == str(tmp_path / "archive.sqlite3")
    assert not (tmp_path / "archive.sqlite3").exists()


@pytest.mark.parametrize(
    "platform,env,expected",
    [
        ("win32", {"LOCALAPPDATA": "/windows/data"}, "/windows/data/iris-llm/memory.sqlite3"),
        ("darwin", {}, "/home/dave/Library/Application Support/iris-llm/memory.sqlite3"),
        ("linux", {"XDG_DATA_HOME": "/linux/data"}, "/linux/data/iris-llm/memory.sqlite3"),
        ("linux", {}, "/home/dave/.local/share/iris-llm/memory.sqlite3"),
    ],
)
def test_platform_data_locations(platform, env, expected):
    with (
        patch("iris_memory.repository.sys.platform", platform),
        patch.dict("os.environ", env, clear=True),
        patch("iris_memory.repository.Path.home", return_value=Path("/home/dave")),
    ):
        assert default_database_path() == Path(expected)


def test_real_http_text_restart_continuity(settings):
    requests = []
    for number in range(2):
        brain, client = make_brain(settings, requests)
        with client, HTTPServer(("127.0.0.1", 0), handler_for(brain, "test")) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with httpx.Client(trust_env=False) as transport:
                    reply = transport.post(
                        f"http://127.0.0.1:{server.server_port}/v1/text",
                        headers={
                            "Authorization": "Bearer test",
                            "X-Iris-Session": str(uuid4()),
                            "Content-Type": "text/plain",
                        },
                        content="The project needs a green roof." if number == 0 else "What roof?",
                    )
                    assert reply.status_code == 200
            finally:
                server.shutdown()
                thread.join()
                brain.lifetime.close()
    chat_requests = [
        req
        for req in requests
        if not req["messages"][0]["content"].startswith("Extract memory metadata")
    ]
    assert chat_requests[-1]["messages"][1]["content"] == "The project needs a green roof."
    assert len(exchanges(settings)) == 2


def test_schema_migration_failure_rolls_back_all_new_tables(tmp_path):
    path = tmp_path / "archive.sqlite3"
    with sqlite3.connect(path) as db:
        # Force failure after the migration has already created its first table.
        db.execute("CREATE TABLE conversations (unrelated TEXT)")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        SQLiteMemory(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert db.execute("SELECT name FROM sqlite_master WHERE name='owners'").fetchone() is None


def test_deleted_archive_is_not_silently_recreated(settings):
    memory = SQLiteMemory(database_path(settings))
    database_path(settings).unlink()
    with pytest.raises(sqlite3.OperationalError):
        memory.active_conversation("default")
    assert not database_path(settings).exists()
