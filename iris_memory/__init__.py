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
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from iris_memory.hierarchy import Prepared
from uuid import uuid4

from iris_memory.controls import MemoryPolicyError
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
                from iris_memory.semantic import SCHEMA

                for statement in SCHEMA:
                    db.execute(statement)
                version = 3
            if version == 3 and create:
                db.execute(
                    "CREATE TABLE memory_policy (owner TEXT PRIMARY KEY REFERENCES owners(id), "
                    "private INTEGER NOT NULL DEFAULT 0 CHECK(private IN (0,1)), "
                    "epoch INTEGER NOT NULL DEFAULT 0)"
                )
                db.execute("PRAGMA user_version=4")
                version = 4
            if version == 4 and create:
                from iris_memory.details import SCHEMA as DETAILS_SCHEMA

                for statement in DETAILS_SCHEMA:
                    db.execute(statement)
                version = 5
            if version == 5 and create:
                from iris_memory.hierarchy import SCHEMA as HIERARCHY_SCHEMA

                for statement in HIERARCHY_SCHEMA:
                    db.execute(statement)
                version = 6
            if version >= 2 and create:
                try:
                    db.execute("SELECT rowid FROM chunks_fts LIMIT 0")
                except sqlite3.OperationalError as error:
                    raise ValueError(
                        "Conversation memory needs a working SQLite FTS5 index. "
                        "Check your Python installation and archive."
                    ) from error
            if version not in {1, 2, 3, 4, 5, 6}:
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

    def append(
        self,
        owner: str,
        conversation: str,
        exchange: Exchange,
        *,
        expected_epoch: int | None = None,
        detail_labels: tuple[str, ...] = (),
        source_conversations: tuple[str, ...] = (),
        followup_conversation: str | None = None,
        hierarchy: Prepared | None = None,
    ) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            private, epoch = self._policy(db, owner)
            if private or (expected_epoch is not None and expected_epoch != epoch):
                raise MemoryPolicyError("Conversation memory policy changed; turn was not saved")
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
            if hierarchy is not None:
                from iris_memory.hierarchy import Hierarchy

                Hierarchy.commit(db, owner, exchange.id, hierarchy)
                source_conversations = tuple(
                    dict.fromkeys((*source_conversations, *hierarchy.source_conversations))
                )
            if db.execute("PRAGMA user_version").fetchone()[0] >= 5:
                for label in detail_labels:
                    db.execute(
                        "INSERT OR IGNORE INTO detail_uses VALUES (?,?,?)",
                        (owner, label, conversation),
                    )
                for source in source_conversations:
                    if source != conversation:
                        db.execute(
                            "INSERT OR IGNORE INTO conversation_uses VALUES (?,?,?)",
                            (owner, source, conversation),
                        )
                    db.execute(
                        "INSERT OR IGNORE INTO detail_uses SELECT owner,label,? FROM detail_uses "
                        "WHERE owner=? AND conversation=?",
                        (conversation, owner, source),
                    )
                if followup_conversation is not None:
                    db.execute(
                        "INSERT INTO companion_topics(owner,conversation,offered_at) "
                        "VALUES (?,?,?) "
                        "ON CONFLICT(owner,conversation) DO UPDATE SET "
                        "offered_at=excluded.offered_at",
                        (owner, followup_conversation, datetime.now(UTC).isoformat()),
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
            if version >= 3:
                derived["embedding_models"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT m.id,m.dimensions,count(v.chunk) AS indexed_chunks "
                        "FROM embedding_models m JOIN embedding_vectors v ON v.model=m.id "
                        "JOIN chunks c ON c.id=v.chunk WHERE c.owner=? GROUP BY m.id",
                        (owner,),
                    )
                ]
            if version >= 4:
                private, epoch = self._policy(db, owner)
                derived["memory_policy"] = [{"private": private, "epoch": epoch}]
            if version >= 5:
                derived["remembered_details"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT label,text,updated_at FROM memory_details WHERE owner=?", (owner,)
                    )
                ]
                derived["detail_uses"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT label,conversation FROM detail_uses WHERE owner=?", (owner,)
                    )
                ]
                derived["conversation_uses"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT source,target FROM conversation_uses WHERE owner=?", (owner,)
                    )
                ]
                derived["companion_topics"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT conversation,blocked,offered_at FROM companion_topics "
                        "WHERE owner=?",
                        (owner,),
                    )
                ]
            if version >= 6:
                for table in ("memory_facts", "memory_topics"):
                    derived[table] = [
                        dict(row)
                        for row in db.execute(f"SELECT * FROM {table} WHERE owner=?", (owner,))
                    ]
                derived["detail_supersessions"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT label FROM detail_supersessions WHERE owner=?", (owner,)
                    )
                ]
                derived["memory_attention"] = [
                    dict(row)
                    for row in db.execute(
                        "SELECT a.*,e.created_at AS source_date FROM memory_attention a "
                        "JOIN memory_topics t ON t.id=a.topic JOIN exchanges e ON e.id=a.source "
                        "WHERE t.owner=?",
                        (owner,),
                    )
                ]
            return {
                **derived,
                "schema_version": version,
                "owner": owner,
                "conversations": conversations,
                "exchanges": exchanges,
            }

    @staticmethod
    def _policy(db: sqlite3.Connection, owner: str) -> tuple[bool, int]:
        if db.execute("PRAGMA user_version").fetchone()[0] < 4:
            return False, 0
        row = db.execute(
            "SELECT private,epoch FROM memory_policy WHERE owner=?", (owner,)
        ).fetchone()
        return (bool(row[0]), int(row[1])) if row else (False, 0)

    @staticmethod
    def _bump_policy(db: sqlite3.Connection, owner: str) -> None:
        if db.execute("PRAGMA user_version").fetchone()[0] >= 4:
            db.execute("INSERT OR IGNORE INTO owners VALUES (?)", (owner,))
            db.execute(
                "INSERT INTO memory_policy(owner,epoch) VALUES (?,1) "
                "ON CONFLICT(owner) DO UPDATE SET epoch=epoch+1",
                (owner,),
            )

    def policy(self, owner: str) -> tuple[bool, int]:
        with self.connect() as db:
            return self._policy(db, owner)

    def set_private(self, owner: str, enabled: bool) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current, _ = self._policy(db, owner)
            if current != enabled:
                self._bump_policy(db, owner)
                db.execute("UPDATE memory_policy SET private=? WHERE owner=?", (enabled, owner))

    @staticmethod
    def _deletion_scope(
        db: sqlite3.Connection, owner: str, conversation: str
    ) -> tuple[tuple[str, str | None], ...]:
        if db.execute("PRAGMA user_version").fetchone()[0] < 5:
            return ()
        return tuple(
            (str(r[0]), r[1])
            for r in db.execute(
                "WITH RECURSIVE affected(id) AS (SELECT id FROM conversations "
                "WHERE owner=? AND id=? "
                "UNION SELECT u.target FROM conversation_uses u JOIN affected a ON u.source=a.id "
                "WHERE u.owner=?) SELECT a.id,(SELECT id FROM exchanges e WHERE e.owner=? "
                "AND e.conversation=a.id ORDER BY sequence DESC LIMIT 1) "
                "FROM affected a ORDER BY a.id",
                (owner, conversation, owner, owner),
            )
        )

    def deletion_scope(self, owner: str, conversation: str) -> tuple[tuple[str, str | None], ...]:
        with self.connect() as db:
            return self._deletion_scope(db, owner, conversation)

    def forget_target(
        self, owner: str, conversation: str | None = None
    ) -> tuple[str, str | None, int] | None:
        with self.connect() as db:
            db.execute("BEGIN")
            row = db.execute(
                "SELECT id FROM conversations WHERE owner=? AND id=COALESCE(?, "
                "(SELECT conversation FROM active_conversations WHERE owner=?))",
                (owner, conversation, owner),
            ).fetchone()
            if row is None:
                return None
            conversation = str(row[0])
            count = db.execute(
                "SELECT count(*) FROM exchanges WHERE owner=? AND conversation=?",
                (owner, conversation),
            ).fetchone()[0]
            latest = db.execute(
                "SELECT id FROM exchanges WHERE owner=? AND conversation=? "
                "ORDER BY sequence DESC LIMIT 1",
                (owner, conversation),
            ).fetchone()
            return conversation, str(latest[0]) if latest else None, int(count)

    def forget_current(
        self,
        owner: str,
        conversation: str,
        revision: str | None,
        *,
        expected_epoch: int | None = None,
        scope: tuple[tuple[str, str | None], ...] | None = None,
        require_active: bool = True,
    ) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            private, epoch = self._policy(db, owner)
            active = db.execute(
                "SELECT conversation FROM active_conversations WHERE owner=?", (owner,)
            ).fetchone()
            latest = db.execute(
                "SELECT id FROM exchanges WHERE owner=? AND conversation=? "
                "ORDER BY sequence DESC LIMIT 1",
                (owner, conversation),
            ).fetchone()
            if (
                private
                or (expected_epoch is not None and epoch != expected_epoch)
                or (require_active and (not active or active[0] != conversation))
                or (scope is not None and self._deletion_scope(db, owner, conversation) != scope)
                or (str(latest[0]) if latest else None) != revision
            ):
                raise MemoryPolicyError(
                    "The current saved conversation changed; request forgetting again"
                )
            for target, _ in self._deletion_scope(db, owner, conversation) or (
                (conversation, None),
            ):
                db.execute("DELETE FROM conversations WHERE owner=? AND id=?", (owner, target))
            if not db.execute(
                "SELECT 1 FROM active_conversations WHERE owner=?", (owner,)
            ).fetchone():
                self._new_conversation(db, owner)
            self._bump_policy(db, owner)

    def delete(self, owner: str, conversation: str | None) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if conversation is None:
                db.execute("DELETE FROM conversations WHERE owner=?", (owner,))
                if db.execute("PRAGMA user_version").fetchone()[0] >= 5:
                    db.execute("DELETE FROM memory_details WHERE owner=?", (owner,))
            else:
                scope = self._deletion_scope(db, owner, conversation)
                for target, _ in scope:
                    if target != conversation:
                        db.execute(
                            "DELETE FROM conversations WHERE owner=? AND id=?", (owner, target)
                        )
                deleted = db.execute(
                    "DELETE FROM conversations WHERE owner=? AND id=?", (owner, conversation)
                )
                if not deleted.rowcount:
                    raise ValueError("No matching conversation for this owner")
            self._bump_policy(db, owner)


def make_memory(settings: Settings) -> SQLiteMemory | None:
    return SQLiteMemory(database_path(settings)) if settings.memory_enabled else None
