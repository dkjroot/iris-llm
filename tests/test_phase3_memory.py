"""Frozen archive upgrades, recovery and source retention across process boundaries."""

import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

from iris_chat_engines.messages import Message
from iris_memory import Exchange, SQLiteMemory, migrations
from iris_memory.embeddings import EmbeddingError
from iris_memory.hierarchy import Hierarchy
from iris_memory.index import MemoryIndex
from iris_memory.models import Summary
from iris_memory.semantic import SEMANTIC_CANDIDATE_LIMIT, SemanticMemory
from iris_settings import Settings

FIXTURES = Path(__file__).parent / "fixtures" / "memory"


def fixture_archive(tmp_path, version):
    path = tmp_path / "memory.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript((FIXTURES / f"v{version}.sql").read_text())
    return path


def source_rows(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT * FROM exchanges ORDER BY sequence").fetchall()


@pytest.mark.parametrize("version", range(1, 11))
def test_frozen_supported_archives_inspect_then_upgrade_without_source_loss(tmp_path, version):
    path = fixture_archive(tmp_path, version)
    before = path.read_bytes()
    originals = source_rows(path)
    inspected = SQLiteMemory(path, create=False)
    assert len(inspected.export("alice")["exchanges"]) == 3
    assert path.read_bytes() == before
    archive = SQLiteMemory(path)
    assert source_rows(path) == originals
    assert archive.current_conversation("alice") == "current"
    assert archive.current_conversation("bob") == "other"
    assert archive.policy("alice") == ((True, 7) if version >= 4 else (False, 0))
    with archive.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert (
            db.execute(
                "SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'greenhouse'"
            ).fetchone()[0]
            == 2
        )
        assert db.execute("SELECT count(*) FROM recall_blocks").fetchone()[0] == 2
        if version >= 3:
            assert db.execute("SELECT count(*) FROM embedding_vectors").fetchone()[0] == 1
        if version >= 5:
            assert db.execute("SELECT text FROM memory_details").fetchone()[0] == "Alice"
            assert tuple(db.execute("SELECT * FROM conversation_uses").fetchone()) == (
                "alice",
                "old",
                "dependent",
            )
        if version >= 6:
            assert db.execute("SELECT value FROM memory_facts").fetchone()[0] == "rainwater storage"
        if version >= 9:
            assert db.execute("SELECT attempts FROM embedding_failures").fetchone()[0] == 3
    upgraded = path.read_bytes()
    SQLiteMemory(path)
    assert path.read_bytes() == upgraded


@pytest.mark.parametrize("target", range(1, 11))
def test_interruption_after_each_migration_rolls_back_entire_upgrade(tmp_path, target):
    path = fixture_archive(tmp_path, target - 1) if target > 1 else tmp_path / "memory.sqlite3"
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
    original = migrations.MIGRATIONS[target - 1]

    def interrupt(db):
        original.apply(db)
        raise KeyboardInterrupt("synthetic interruption before version commit")

    replacements = tuple(
        migrations.Migration(m.version, m.name, interrupt) if m.version == target else m
        for m in migrations.MIGRATIONS
    )
    with patch.object(migrations, "MIGRATIONS", replacements), pytest.raises(KeyboardInterrupt):
        SQLiteMemory(path)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before
    assert len(SQLiteMemory(path).export("alice")["exchanges"]) == (3 if target > 1 else 0)


@pytest.mark.parametrize("create", [False, True])
def test_future_schema_is_rejected_without_mutation(tmp_path, create):
    path = fixture_archive(tmp_path, 10)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=11")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported.*11"):
        SQLiteMemory(path, create=create)
    assert path.read_bytes() == before


def test_nested_read_snapshot_stays_coherent_and_disallows_writes(tmp_path):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conversation = archive.active_conversation("alice")
    archive.append("alice", conversation, Exchange("first", "human", "Original", "Reply"))
    # WAL allows a deterministic concurrent commit during a reader transaction.
    # This is a test arrangement; Iris does not change the production journal mode.
    with archive.connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
    writer = SQLiteMemory(archive.path)
    with archive.snapshot() as db:
        assert archive.revision("alice", conversation) == "first"
        writer.append("alice", conversation, Exchange("second", "human", "Later", "Reply"))
        writer.set_private("alice", True)
        with archive.snapshot() as nested:
            assert nested is db
            assert archive.policy("alice") == (False, 0)
            assert [r.id for r in archive.recent("alice", conversation, 10, 1000)] == ["first"]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("DELETE FROM exchanges")
    assert archive.policy("alice").private
    assert archive.revision("alice", conversation) == "second"


def test_summary_queue_rotates_partial_work_and_survives_failure_restart(tmp_path):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    first = archive.start_conversation("alice")
    for number in range(14):
        archive.append("alice", first, Exchange(f"a{number}", "human", "First", "Reply"))
    second = archive.start_conversation("alice")
    archive.append("alice", second, Exchange("b", "human", "Second", "Reply"))
    index = MemoryIndex(archive)
    assert index.next_conversation("alice", "") == first
    assert index.save("alice", first, "a13", 0, 12, Summary(notes=[]), expected_epoch=0)
    restarted = MemoryIndex(SQLiteMemory(archive.path))
    assert restarted.next_conversation("alice", "") == second
    restarted.failed("alice", second)
    assert restarted.next_conversation("alice", "") == first
    with archive.connect() as db:
        db.execute("UPDATE summary_jobs SET attempts=3 WHERE conversation=?", (second,))
    assert MemoryIndex(SQLiteMemory(archive.path)).pending("alice", second) is None
    restarted.retry("alice")
    assert restarted.pending("alice", second)["attempts"] == 0
    assert restarted.save("alice", first, "a13", 12, 14, Summary(notes=[]), expected_epoch=0)
    assert restarted.next_conversation("alice", "") == second


class Encoder:
    model_id = "phase3-synthetic"
    dimensions = 3

    def __init__(self):
        self.fail = False
        self.during = None

    def encode(self, texts):
        if self.during:
            self.during()
        if self.fail:
            raise EmbeddingError("synthetic failure")
        return np.asarray(
            [[1, 0, 0] if "rainwater" in t or "collection" in t else [0, 1, 0] for t in texts],
            dtype=np.float32,
        )


def test_vector_failure_backoff_exhaustion_and_retry_are_durable_and_owner_scoped(tmp_path):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    for owner in ("alice", "bob"):
        conversation = archive.start_conversation(owner)
        for number in range(2):
            archive.append(
                owner, conversation, Exchange(f"{owner}{number}", "human", "Source", "Reply")
            )
    encoder = Encoder()
    semantic = SemanticMemory(archive, encoder)
    encoder.fail = True
    with pytest.raises(EmbeddingError):
        semantic.index_batch("alice", 2)
    encoder.fail = False
    reopened = SemanticMemory(SQLiteMemory(archive.path), encoder)
    assert reopened.index_batch("alice", 2) == 2  # Failure does not starve later sources.
    assert reopened.index_batch("alice", 2) == 0
    assert reopened.pending("alice") == 2
    for attempt in (2, 3):
        with archive.connect() as db:
            db.execute("UPDATE embedding_failures SET retry_at=0")
        encoder.fail = True
        with pytest.raises(EmbeddingError):
            reopened.index_batch("alice", 2)
        with archive.connect() as db:
            assert {r[0] for r in db.execute("SELECT attempts FROM embedding_failures")} == {
                attempt
            }
    encoder.fail = False
    assert SemanticMemory(SQLiteMemory(archive.path), encoder).index_batch("alice") == 0
    assert reopened.index_batch("bob") == 4
    MemoryIndex(archive).retry("bob")
    assert reopened.index_batch("alice") == 0
    MemoryIndex(archive).retry("alice")
    assert reopened.index_batch("alice") == 2
    assert reopened.pending("alice") == 0
    with archive.connect() as db:
        assert db.execute("SELECT count(*) FROM embedding_failures").fetchone()[0] == 0


@pytest.mark.parametrize("action", ["privacy", "rebuild", "reset", "delete"])
def test_vector_inference_cannot_commit_across_policy_or_rebuild_boundary(tmp_path, action):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conversation = archive.start_conversation("alice")
    archive.append("alice", conversation, Exchange("source", "human", "Source", "Reply"))
    encoder = Encoder()
    semantic = SemanticMemory(archive, encoder)

    def invalidate():
        if action == "privacy":
            archive.set_private("alice", True)
            archive.set_private("alice", False)
        elif action == "rebuild":
            MemoryIndex(archive).rebuild("alice")
        elif action == "reset":
            semantic.reset("alice")
        else:
            archive.delete("alice", conversation)

    encoder.during = invalidate
    assert semantic.index_batch("alice") == 0
    with archive.connect() as db:
        assert db.execute("SELECT count(*) FROM embedding_vectors").fetchone()[0] == 0
    assert SQLiteMemory(archive.path).policy("alice").epoch > 0


def test_stopped_backup_restore_rebuild_keeps_full_sources_and_forgetting(tmp_path):
    path = fixture_archive(tmp_path, 9)
    archive = SQLiteMemory(path)
    archive.set_private("alice", False)
    original = "Complete original café 🌱\n" * 4000
    archive.append("alice", "current", Exchange("long", "human", original, original))
    # Forget also removes the dependent copy recorded by provenance.
    archive.delete("alice", "old")
    retained = source_rows(path)
    assert {r[1] for r in retained} == {"source-2", "source-4", "long"}
    archive.set_private("alice", True)
    backup = tmp_path / "stopped-backup.sqlite3"
    shutil.copy2(path, backup)  # No live connections: the documented stopped procedure.
    restored_path = tmp_path / "restored.sqlite3"
    shutil.copy2(backup, restored_path)
    restored = SQLiteMemory(restored_path)
    assert source_rows(restored_path) == retained
    assert restored.policy("alice").private
    MemoryIndex(restored).rebuild("alice")
    restarted = SQLiteMemory(restored_path)
    assert source_rows(restored_path) == retained
    assert restarted.policy("alice").private
    with restarted.connect() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("SELECT text FROM memory_details").fetchone()[0] == "Alice"
        assert not db.execute(
            "SELECT 1 FROM chunks WHERE exchange IN ('source-1','source-3')"
        ).fetchall()
        assert not db.execute("SELECT 1 FROM memory_facts").fetchall()
        assert not db.execute("SELECT 1 FROM embedding_failures").fetchall()
    assert restarted.export("alice")["exchanges"][-1]["user_text"] == original


@pytest.mark.parametrize("fence", ["", "```json\n{}\n```", "```\n{}\n```"])
def test_hierarchy_accepts_complete_fenced_json_without_weakening_grounding(tmp_path, fence):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conversation = archive.start_conversation("alice")
    backend = Mock()
    hierarchy = Hierarchy(archive, Settings(), backend)
    fact = dict(
        key="name",
        value="Alice",
        kind="identity",
        scope="real",
        lifetime="stable",
        evidence="My name is Alice",
    )
    payload = json.dumps({"facts": [fact]})
    backend.complete.return_value = Message(
        role="assistant", content=fence.replace("{}", payload) if fence else payload
    )
    assert hierarchy.prepare("alice", conversation, "My name is Alice") is not None
    assert hierarchy.prepare("alice", conversation, "My name is Bob") is None
    assert not archive.export("alice")["exchanges"]


@pytest.mark.parametrize(
    "payload,reason",
    [
        ('{"facts":[{"private secret":"never log me"}]}', "facts.0.key: missing"),
        ('{"private secret":"never log me"}', "<field>: extra_forbidden"),
        ('{"facts":', "response: json_invalid"),
        ('Here is JSON: {"facts":[]}', "response: json_invalid"),
        ('{"facts":[{"key":"name","value":123}]}', "facts.0.value: string_type"),
    ],
)
def test_hierarchy_diagnostics_reject_invalid_updates_without_logging_text(
    tmp_path, caplog, payload, reason
):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conversation = archive.start_conversation("alice")
    backend = Mock()
    backend.complete.return_value = Message(role="assistant", content=payload)
    hierarchy = Hierarchy(archive, Settings(), backend)
    assert hierarchy.prepare("alice", conversation, "My private source") is None
    assert reason in caplog.text
    assert all(
        secret not in caplog.text
        for secret in ("private secret", "never log me", "My private source")
    )
    assert not hierarchy.facts("alice")


def test_hierarchy_truncation_is_identified_without_logging_partial_json(tmp_path, caplog):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    backend = Mock()
    reply = Message(role="assistant", content='{"facts": ["private unfinished')
    reply._finish_reason = "length"
    backend.complete.return_value = reply
    hierarchy = Hierarchy(archive, Settings(), backend)
    assert hierarchy.prepare("alice", archive.start_conversation("alice"), "Hello") is None
    assert "truncated output (model token limit)" in caplog.text
    assert "private unfinished" not in caplog.text


def test_large_archive_old_topic_remains_lexically_recallable_beyond_vector_horizon(tmp_path):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conversation = archive.start_conversation("alice")
    # Bulk fixture construction keeps this test cheap while using the real chunker.
    from iris_memory.index import index_exchange

    with archive.connect() as db:
        for number in range(SEMANTIC_CANDIDATE_LIMIT // 2 + 10):
            db.execute(
                "INSERT INTO exchanges(id,owner,conversation,created_at,origin,user_text,"
                "assistant_text,tool_names) VALUES (?,?,?,?,'human',?,?,'[]')",
                (
                    f"source{number}",
                    "alice",
                    conversation,
                    "2000-01-01T00:00:00+00:00" if number == 0 else "2026-01-01T00:00:00+00:00",
                    "rainwater greenhouse" if number == 0 else "unrelated cinema",
                    "Reply",
                ),
            )
        for row in db.execute("SELECT * FROM exchanges ORDER BY sequence"):
            index_exchange(db, row)
    semantic = SemanticMemory(archive, Encoder())
    while semantic.pending("alice"):
        assert semantic.index_batch("alice", 32)
    # Document the existing bounded semantic candidate horizon rather than changing
    # retrieval ranking as an unmeasured side effect of the persistence refactor.
    assert not semantic.search("alice", "collection", explicit=True)
    dated = semantic.search(
        "alice", "collection", explicit=True, end=datetime(2001, 1, 1, tzinfo=UTC)
    )
    assert dated[0]["exchange"] == "source0"
    index = MemoryIndex(archive)
    assert (
        index.recall_sources("alice", ["rainwater", "greenhouse"], None, None)[0]["exchange"]
        == "source0"
    )


@pytest.mark.parametrize("target", range(3, 10))
def test_late_migration_failure_rolls_back_earlier_upgrades_too(tmp_path, target):
    path = fixture_archive(tmp_path, 1)
    with sqlite3.connect(path) as db:
        before = list(db.iterdump())
    original = migrations.MIGRATIONS[target - 1]

    def fail(db):
        original.apply(db)
        raise sqlite3.OperationalError("synthetic late failure")

    replacements = tuple(
        migrations.Migration(m.version, m.name, fail) if m.version == target else m
        for m in migrations.MIGRATIONS
    )
    with (
        patch.object(migrations, "MIGRATIONS", replacements),
        pytest.raises(sqlite3.OperationalError),
    ):
        SQLiteMemory(path)
    with sqlite3.connect(path) as db:
        assert list(db.iterdump()) == before


def test_summary_snapshot_keeps_revision_sources_and_policy_together(tmp_path):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conversation = archive.start_conversation("alice")
    archive.append("alice", conversation, Exchange("first", "human", "First", "Reply"))
    with archive.connect() as db:
        db.execute("PRAGMA journal_mode=WAL")
    writer = SQLiteMemory(archive.path)
    index = MemoryIndex(archive)
    original_sources = index.sources

    def append_during_sources(*args):
        writer.append("alice", conversation, Exchange("second", "human", "Second", "Reply"))
        return original_sources(*args)

    with patch.object(index, "sources", append_during_sources):
        snapshot = index.summary_snapshot("alice", conversation)
    assert snapshot.revision == "first"
    assert [r["id"] for r in snapshot.sources] == ["first"]
    assert snapshot.job["pending_count"] == 1
    assert not index.save(
        "alice",
        conversation,
        snapshot.revision,
        0,
        1,
        Summary(notes=[]),
        expected_epoch=snapshot.policy.epoch,
    )


def test_delete_previous_requeues_behind_existing_work_without_capacity_limit(tmp_path):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    for number in range(65):
        conversation = archive.start_conversation("alice")
        archive.append(
            "alice", conversation, Exchange(f"source{number}", "human", "Source", "Reply")
        )
    archive.append("alice", conversation, Exchange("last", "human", "Last", "Reply"))
    assert archive.delete_previous("alice", expected_epoch=0) == "last"
    with archive.connect() as db:
        assert db.execute("SELECT count(*) FROM summary_jobs").fetchone()[0] == 65
        assert (
            db.execute(
                "SELECT conversation FROM summary_jobs ORDER BY last_served DESC LIMIT 1"
            ).fetchone()[0]
            == conversation
        )


def test_sqlite_backup_recovers_committed_wal_after_unclean_exit(tmp_path):
    import subprocess
    import sys

    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conversation = archive.start_conversation("alice")
    archive.append(
        "alice", conversation, Exchange("original", "human", "Original café 🌱", "Reply")
    )
    # A subprocess exits without closing SQLite, leaving a committed WAL frame.
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, sqlite3, sys; d=sqlite3.connect(sys.argv[1]); "
            "d.execute('PRAGMA journal_mode=WAL'); d.execute('PRAGMA wal_autocheckpoint=0'); "
            "d.execute(\"UPDATE exchanges SET assistant_text='Committed before crash'\"); "
            "d.commit(); os._exit(0)",
            str(archive.path),
        ],
        check=True,
    )
    assert archive.path.with_name(archive.path.name + "-wal").is_file()
    recovered = tmp_path / "recovered.sqlite3"
    with sqlite3.connect(f"{archive.path.as_uri()}?mode=rw", uri=True) as source:
        with sqlite3.connect(recovered) as destination:
            source.backup(destination)
    restored = SQLiteMemory(recovered)
    row = restored.export("alice")["exchanges"][0]
    assert row["user_text"] == "Original café 🌱"
    assert row["assistant_text"] == "Committed before crash"
    with restored.connect() as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()


def test_index_command_exits_when_pending_vectors_are_in_backoff(tmp_path, capsys):
    from iris_memory.cli import main

    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conversation = archive.start_conversation("alice")
    archive.append("alice", conversation, Exchange("source", "human", "Source", "Reply"))
    encoder = Encoder()
    encoder.fail = True
    with pytest.raises(EmbeddingError):
        SemanticMemory(archive, encoder).index_batch("alice")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"memory_database": str(archive.path), "memory_owner": "alice"}))
    encoder.fail = False
    with patch("iris_memory.cli.MiniLMEncoder", return_value=encoder):
        assert main(["--config", str(config), "index"]) == 1
        assert "2 chunks pending" in capsys.readouterr().out
        assert main(["--config", str(config), "retry"]) == 0
        assert main(["--config", str(config), "index"]) == 0
    assert "Semantic index ready" in capsys.readouterr().out


@pytest.mark.parametrize("valid", [True, False])
def test_summary_fence_and_validation_diagnostics_preserve_sources(tmp_path, caplog, valid):
    from iris_memory.context import MemoryContext

    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conversation = archive.start_conversation("alice")
    archive.append(
        "alice", conversation, Exchange("source", "human", "Private source text", "Reply")
    )
    backend = Mock()
    backend.complete.return_value = Message(
        role="assistant",
        content='```json\n{"notes": []}\n```' if valid else '{"private secret":"never log me"}',
    )
    index = MemoryIndex(archive)
    context = MemoryContext(Settings(), index, backend)
    assert context.checkpoint("alice", conversation, force=True) is valid
    if not valid:
        assert "<field>: extra_forbidden" in caplog.text
    assert all(
        text not in caplog.text
        for text in ("private secret", "never log me", "Private source text")
    )
    assert archive.export("alice")["exchanges"][0]["user_text"] == "Private source text"


def test_failed_vector_inference_does_not_recreate_failure_state_after_reset(tmp_path):
    archive = SQLiteMemory(tmp_path / "memory.sqlite3")
    conversation = archive.start_conversation("alice")
    archive.append("alice", conversation, Exchange("source", "human", "Source", "Reply"))
    encoder = Encoder()
    semantic = SemanticMemory(archive, encoder)
    encoder.fail = True
    encoder.during = lambda: semantic.reset("alice")
    with pytest.raises(EmbeddingError):
        semantic.index_batch("alice")
    with archive.connect() as db:
        assert not db.execute("SELECT 1 FROM embedding_failures").fetchall()
    assert semantic.pending("alice") == 2
