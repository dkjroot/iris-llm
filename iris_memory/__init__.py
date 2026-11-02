"""Durable, user-facing exchanges; never serialise live model messages."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import uuid4

from iris_settings import Settings

AUTOMATIC_EVENT = "[Automatic companion event, not human speech: offer a conversation opener.]"


def default_database_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "iris-llm" / "memory.sqlite3"


def database_path(settings: Settings) -> Path:
    return (settings.memory_database or default_database_path()).expanduser().resolve()


@dataclass(frozen=True)
class Exchange:
    id: str
    origin: Literal["human", "automatic"]
    user_text: str
    assistant_text: str
    tool_names: tuple[str, ...] = ()


class MemoryStore(Protocol):
    def active_conversation(self, owner: str) -> str: ...
    def start_conversation(self, owner: str) -> str: ...
    def recent(self, owner: str, conversation: str, limit: int, chars: int) -> list[Exchange]: ...
    def append(self, owner: str, conversation: str, exchange: Exchange) -> None: ...
    def revision(self, owner: str, conversation: str) -> str | None: ...


class SQLiteMemory:
    """One short-lived connection per operation, with atomic writes and a finite lock wait."""

    def __init__(self, path: Path, *, create: bool = True) -> None:
        self.path = path.expanduser().resolve()
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        elif not self.path.is_file():
            raise ValueError("No memory archive exists at the configured location")
        with self.connect(create=create) as db:
            db.execute("BEGIN IMMEDIATE" if create else "BEGIN")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0 and create:
                # Migration 1. All schema changes and the version commit together.
                for statement in (
                    "CREATE TABLE owners (id TEXT PRIMARY KEY)",
                    "CREATE TABLE conversations (id TEXT PRIMARY KEY, owner TEXT NOT NULL "
                    "REFERENCES owners(id), created_at TEXT NOT NULL, UNIQUE(owner, id))",
                    "CREATE TABLE active_conversations (owner TEXT PRIMARY KEY REFERENCES "
                    "owners(id), conversation TEXT NOT NULL, FOREIGN KEY(owner, conversation) "
                    "REFERENCES conversations(owner, id) ON DELETE CASCADE)",
                    "CREATE TABLE exchanges (sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "id TEXT NOT NULL UNIQUE, owner TEXT NOT NULL, conversation TEXT NOT NULL, "
                    "created_at TEXT NOT NULL, origin TEXT NOT NULL CHECK(origin IN "
                    "('human','automatic')), user_text TEXT NOT NULL, "
                    "assistant_text TEXT NOT NULL, "
                    "tool_names TEXT NOT NULL, FOREIGN KEY(owner, conversation) REFERENCES "
                    "conversations(owner, id) ON DELETE CASCADE)",
                    "CREATE INDEX conversation_exchanges ON exchanges(conversation, sequence)",
                    "PRAGMA user_version=1",
                ):
                    db.execute(statement)
                version = 1
            if version == 1 and create:
                from iris_memory.index import migrate

                migrate(db)
                version = 2
            if version == 2 and create:
                try:
                    db.execute("SELECT rowid FROM chunks_fts LIMIT 0")
                except sqlite3.OperationalError as error:
                    raise ValueError(
                        "Conversation memory needs a working SQLite FTS5 index. "
                        "Check your Python installation and archive."
                    ) from error
            if version not in {1, 2}:
                raise ValueError("Unsupported Iris memory schema version")

    @contextmanager
    def connect(self, *, create: bool = False) -> Iterator[sqlite3.Connection]:
        # mode=rw avoids accidentally recreating a deleted archive during a running session.
        mode = "rwc" if create else "rw"
        db = sqlite3.connect(f"{self.path.as_uri()}?mode={mode}", uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _new_conversation(db: sqlite3.Connection, owner: str) -> str:
        conversation = str(uuid4())
        db.execute("INSERT OR IGNORE INTO owners VALUES (?)", (owner,))
        db.execute(
            "INSERT INTO conversations VALUES (?, ?, ?)",
            (conversation, owner, datetime.now(UTC).isoformat()),
        )
        db.execute(
            "INSERT INTO active_conversations VALUES (?, ?) ON CONFLICT(owner) "
            "DO UPDATE SET conversation=excluded.conversation",
            (owner, conversation),
        )
        return conversation

    def active_conversation(self, owner: str) -> str:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT conversation FROM active_conversations WHERE owner=?", (owner,)
            ).fetchone()
            return str(row[0]) if row else self._new_conversation(db, owner)

    def start_conversation(self, owner: str) -> str:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("PRAGMA user_version").fetchone()[0] >= 2:
                db.execute(
                    "INSERT OR IGNORE INTO recall_blocks SELECT owner,id "
                    "FROM conversations WHERE owner=?",
                    (owner,),
                )
            return self._new_conversation(db, owner)

    def recent(self, owner: str, conversation: str, limit: int, chars: int) -> list[Exchange]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM exchanges WHERE owner=? AND conversation=? "
                "ORDER BY sequence DESC LIMIT ?",
                (owner, conversation, limit),
            )
            result = []
            for row in rows:
                size = len(row["user_text"]) + len(row["assistant_text"])
                if size > chars:
                    break
                chars -= size
                result.append(
                    Exchange(
                        row["id"],
                        row["origin"],
                        row["user_text"],
                        row["assistant_text"],
                        tuple(json.loads(row["tool_names"])),
                    )
                )
            return list(reversed(result))

    def revision(self, owner: str, conversation: str) -> str | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT id FROM exchanges WHERE owner=? AND conversation=? "
                "ORDER BY sequence DESC LIMIT 1",
                (owner, conversation),
            ).fetchone()
            return str(row[0]) if row else None

    def append(self, owner: str, conversation: str, exchange: Exchange) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            values = (
                owner,
                conversation,
                exchange.origin,
                exchange.user_text,
                exchange.assistant_text,
                json.dumps(exchange.tool_names),
            )
            existing = db.execute(
                "SELECT owner, conversation, origin, user_text, assistant_text, tool_names "
                "FROM exchanges WHERE id=?",
                (exchange.id,),
            ).fetchone()
            if existing:
                if tuple(existing) != values:
                    raise ValueError("Memory exchange ID already belongs to another exchange")
                return
            db.execute(
                "INSERT INTO exchanges (id, created_at, owner, conversation, origin, user_text, "
                "assistant_text, tool_names) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (exchange.id, datetime.now(UTC).isoformat(), *values),
            )
            if db.execute("PRAGMA user_version").fetchone()[0] >= 2:
                from iris_memory.index import index_exchange

                row = db.execute("SELECT * FROM exchanges WHERE id=?", (exchange.id,)).fetchone()
                index_exchange(db, row)
                db.execute(
                    "INSERT OR IGNORE INTO summary_jobs(conversation,owner) "
                    "SELECT ?,? WHERE (SELECT count(*) FROM summary_jobs)<64",
                    (conversation, owner),
                )

    def conversations(self, owner: str) -> list[dict[str, object]]:
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT c.id, c.created_at, COUNT(e.id) AS turns, "
                    "c.id=a.conversation AS active FROM conversations c "
                    "LEFT JOIN exchanges e ON e.conversation=c.id "
                    "LEFT JOIN active_conversations a ON a.owner=c.owner "
                    "WHERE c.owner=? GROUP BY c.id ORDER BY c.created_at",
                    (owner,),
                )
            ]

    def export(self, owner: str) -> dict[str, object]:
        with self.connect() as db:
            conversations = [
                dict(row)
                for row in db.execute(
                    "SELECT id, created_at FROM conversations WHERE owner=? ORDER BY created_at",
                    (owner,),
                )
            ]
            exchanges = []
            for row in db.execute(
                "SELECT id, conversation, created_at, origin, user_text, "
                "assistant_text, tool_names "
                "FROM exchanges WHERE owner=? ORDER BY sequence",
                (owner,),
            ):
                item = dict(row)
                item["tool_names"] = json.loads(item["tool_names"])
                exchanges.append(item)
            version = db.execute("PRAGMA user_version").fetchone()[0]
            derived: dict[str, list[dict[str, object]]] = {}
            if version >= 2:
                derived = {
                    "summaries": [
                        dict(r)
                        for r in db.execute(
                            "SELECT * FROM summaries WHERE owner=? ORDER BY through_sequence",
                            (owner,),
                        )
                    ],
                    "notes": [
                        dict(r) for r in db.execute("SELECT * FROM notes WHERE owner=?", (owner,))
                    ],
                    "summary_jobs": [
                        dict(r)
                        for r in db.execute("SELECT * FROM summary_jobs WHERE owner=?", (owner,))
                    ],
                }
                for row in derived["summaries"]:
                    row["payload"] = json.loads(str(row["payload"]))
                for row in derived["notes"]:
                    row["sources"] = json.loads(str(row["sources"]))
            return {
                **derived,
                "schema_version": version,
                "owner": owner,
                "conversations": conversations,
                "exchanges": exchanges,
            }

    def delete(self, owner: str, conversation: str | None) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if conversation is None:
                db.execute("DELETE FROM conversations WHERE owner=?", (owner,))
            else:
                deleted = db.execute(
                    "DELETE FROM conversations WHERE owner=? AND id=?", (owner, conversation)
                )
                if not deleted.rowcount:
                    raise ValueError("No matching conversation for this owner")


def make_memory(settings: Settings) -> SQLiteMemory | None:
    return SQLiteMemory(database_path(settings)) if settings.memory_enabled else None
