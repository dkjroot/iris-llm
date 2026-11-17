"""Durable backlog, derived visibility repairs and configuration replacement."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

import configure
import configure_client
import iris_paths
from iris_memory import Exchange, SQLiteMemory
from iris_memory.index import MemoryIndex
from iris_memory.models import Note, Summary
from iris_paths import atomic_json, runtime_data_root, server_config


def drain(memory, owner, active):
    index = MemoryIndex(memory)
    count = 0
    for _ in range(1100):
        conversation = index.next_conversation(owner, active)
        if conversation is None:
            return count
        pending = index.pending(owner, conversation)
        sources = index.sources(owner, conversation, pending["cursor"])
        assert sources
        assert index.save(
            owner,
            conversation,
            memory.revision(owner, conversation),
            pending["cursor"],
            sources[-1]["sequence"],
            Summary(notes=[]),
        )
        count += 1
    pytest.fail("Summary backlog did not finish")


def test_thousand_conversation_backlog_survives_old_queue_cap_restart_failure_and_rebuild(tmp_path):
    memory = SQLiteMemory(tmp_path / "archive.sqlite3")
    for owner, count in (("a", 1000), ("b", 3)):
        for number in range(count):
            conversation = memory.start_conversation(owner)
            memory.append(
                owner, conversation, Exchange(f"{owner}-{number}", "human", "Source", "Reply")
            )
        memory.start_conversation(owner)
    # Simulate an archive left by the old capped queue, including a failed job.
    with memory.connect() as db:
        db.execute("DELETE FROM summary_jobs WHERE rowid>64")
        failed = db.execute(
            "SELECT conversation FROM summary_jobs ORDER BY rowid LIMIT 1"
        ).fetchone()[0]
        db.execute("UPDATE summary_jobs SET attempts=3 WHERE conversation=?", (failed,))
    restarted = SQLiteMemory(memory.path)
    assert drain(restarted, "b", restarted.current_conversation("b")) == 3
    assert drain(restarted, "a", restarted.current_conversation("a")) == 999
    index = MemoryIndex(restarted)
    index.retry("a")
    assert drain(restarted, "a", restarted.current_conversation("a")) == 1
    index.rebuild("b")
    assert drain(restarted, "b", restarted.current_conversation("b")) == 3
    assert len(restarted.export("a")["exchanges"]) == 1000
    assert len(restarted.export("b")["exchanges"]) == 3


def test_upgrade_repairs_only_partially_superseded_sources_and_preserves_originals(tmp_path):
    memory = SQLiteMemory(tmp_path / "archive.sqlite3")
    index = MemoryIndex(memory)
    conversation = memory.active_conversation("a")
    source = "Robot Sapphire. Greenhouse ventilation."
    memory.append("a", conversation, Exchange("source", "human", source, "Noted"))

    def note(text, sources, supersedes=()):
        return Note(
            kind="user_assertion",
            scope="real",
            status="confirmed",
            text=text,
            sources=sources,
            supersedes=list(supersedes),
        )

    assert index.save(
        "a",
        conversation,
        "source",
        0,
        1,
        Summary(
            notes=[note("Robot Sapphire", ["source"]), note("Greenhouse ventilation", ["source"])]
        ),
    )
    old = next(n for n in index.notes("a", conversation) if "Sapphire" in n["text"])
    memory.append("a", conversation, Exchange("update", "human", "Robot Emerald", "Corrected"))
    assert index.save(
        "a",
        conversation,
        "update",
        1,
        2,
        Summary(notes=[note("Robot Emerald", ["update"], [old["id"]])]),
    )
    with memory.connect() as db:
        db.execute("UPDATE chunks SET superseded=1 WHERE exchange='source'")
        db.execute("DROP TABLE embedding_failures")
        db.execute("ALTER TABLE summary_jobs DROP COLUMN last_served")
        db.execute("PRAGMA user_version=7")
    before = memory.export("a")["exchanges"]
    with memory.connect() as db:
        db.execute(
            "CREATE TRIGGER fail_repair BEFORE UPDATE OF superseded ON chunks "
            "WHEN NEW.superseded=0 BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        SQLiteMemory(memory.path)
    with memory.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 7
        assert (
            db.execute("SELECT MIN(superseded) FROM chunks WHERE exchange='source'").fetchone()[0]
            == 1
        )
        db.execute("DROP TRIGGER fail_repair")
    repaired = SQLiteMemory(memory.path)
    index = MemoryIndex(repaired)
    assert index.recall_sources("a", ["greenhouse"], None, None)
    assert index.recall_evidence("a", conversation, ("source",))
    assert index.search("a", conversation, "greenhouse", "")
    assert all("Sapphire" not in n["text"] for n in index.notes("a", conversation))
    assert repaired.export("a")["exchanges"] == before
    assert SQLiteMemory(memory.path).export("a")["exchanges"] == before
    repaired.delete("a", conversation)
    assert not MemoryIndex(SQLiteMemory(memory.path)).recall_sources(
        "a", ["greenhouse"], None, None
    )


def test_config_resolution_has_explicit_cwd_and_checkout_precedence(tmp_path, monkeypatch):
    checkout, elsewhere = tmp_path / "source", tmp_path / "work"
    checkout.mkdir()
    elsewhere.mkdir()
    (checkout / "iris.py").touch()
    (checkout / "pyproject.toml").touch()
    legacy = checkout / "local-config.json"
    legacy.write_text("{}")
    monkeypatch.setattr(iris_paths, "PROJECT_ROOT", checkout)
    monkeypatch.chdir(elsewhere)
    with pytest.warns(UserWarning, match="checkout configuration"):
        assert server_config() == legacy
    current = elsewhere / "local-config.json"
    current.write_text("{}")
    assert server_config() == current
    assert server_config(legacy) == legacy
    missing = elsewhere / "missing.json"
    with pytest.raises(ValueError, match="does not exist"):
        server_config(missing)
    assert server_config(missing, for_write=True) == missing
    current.unlink()
    (checkout / "pyproject.toml").unlink()
    assert server_config() is None  # An installed module's neighbour is not a config fallback.
    assert server_config(for_write=True) == current


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_installed_worker_assets_are_outside_package(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(iris_paths.sys, "platform", platform)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    root = runtime_data_root()
    assert root != iris_paths.PROJECT_ROOT
    assert root.name == "iris-llm"


@pytest.mark.parametrize("failure", ["serialization", "fsync", "replace"])
def test_atomic_config_failure_leaves_old_file_and_no_temporary_file(tmp_path, failure):
    path = tmp_path / "config.json"
    original = '{"model":"original"}\n'
    path.write_text(original)
    if failure == "serialization":
        with pytest.raises(ValueError):
            atomic_json(path, {"value": float("nan")})
    else:
        with (
            patch(f"iris_paths.os.{failure}", side_effect=OSError("injected")),
            pytest.raises(OSError),
        ):
            atomic_json(path, {"model": "replacement"})
    assert path.read_text() == original
    assert list(tmp_path.iterdir()) == [path]
    atomic_json(path, {"model": "replacement"})
    assert json.loads(path.read_text()) == {"model": "replacement"}


@pytest.mark.parametrize("wizard", [configure, configure_client])
def test_wizards_preserve_explicit_destination_on_replace_failure(tmp_path, monkeypatch, wizard):
    path = tmp_path / "chosen.json"
    original = "{}\n"
    path.write_text(original)
    monkeypatch.setattr("builtins.input", lambda _: "")
    with patch("iris_paths.os.replace", side_effect=OSError("injected")):
        assert wizard.main(["--config", str(path)]) == 1
    assert path.read_text() == original
