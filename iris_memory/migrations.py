"""Atomic ordered schema transitions, independent of archive construction.

The caller owns the transaction. Inspection validates versions without upgrading.
A failure, including an interrupted migration, leaves version and data unchanged.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from iris_memory.schema import (
    DETAIL_SCHEMA,
    HIERARCHY_SCHEMA,
    INDEX_SCHEMA,
    SEARCH_SCHEMA,
    SOURCE_SCHEMA,
    VECTOR_SCHEMA,
)

CURRENT_VERSION = 10
SUPPORTED_VERSIONS = frozenset(range(1, CURRENT_VERSION + 1))


def statements(db: sqlite3.Connection, sql: tuple[str, ...]) -> None:
    for statement in sql:
        if not statement.startswith("PRAGMA user_version"):
            db.execute(statement)


def repair_source_visibility(db: sqlite3.Connection) -> None:
    """Schema 8 repair using a temporary index, without requiring SQLite JSON1."""
    db.execute(
        "CREATE TEMP TABLE repair_note_sources (owner TEXT, conversation TEXT, source TEXT, "
        "superseded INTEGER, PRIMARY KEY(owner,conversation,source,superseded)) WITHOUT ROWID"
    )
    for row in db.execute("SELECT owner,conversation,sources,status FROM notes"):
        db.executemany(
            "INSERT OR IGNORE INTO repair_note_sources VALUES (?,?,?,?)",
            (
                (row["owner"], row["conversation"], source, row["status"] == "superseded")
                for source in json.loads(row["sources"])
            ),
        )
    db.execute(
        "UPDATE chunks SET superseded=0 WHERE superseded=1 "
        "AND EXISTS(SELECT 1 FROM repair_note_sources s WHERE s.owner=chunks.owner "
        "AND s.conversation=chunks.conversation AND s.source=chunks.exchange AND s.superseded=0) "
        "AND EXISTS(SELECT 1 FROM repair_note_sources s WHERE s.owner=chunks.owner "
        "AND s.conversation=chunks.conversation AND s.source=chunks.exchange AND s.superseded=1)"
    )
    db.execute("DROP TABLE repair_note_sources")


def migrate_search(db: sqlite3.Connection) -> None:
    from iris_memory.index import index_exchange

    try:
        db.execute("CREATE VIRTUAL TABLE temp.iris_fts_probe USING fts5(text)")
        db.execute("DROP TABLE temp.iris_fts_probe")
    except sqlite3.OperationalError as error:
        raise ValueError(
            "Conversation memory needs SQLite FTS5. Install Python with FTS5 support."
        ) from error
    for statement in SEARCH_SCHEMA:
        db.execute(statement)
    for row in db.execute("SELECT * FROM exchanges ORDER BY sequence"):
        index_exchange(db, row)
    db.execute(
        "INSERT INTO recall_blocks SELECT c.owner,c.id FROM conversations c "
        "LEFT JOIN active_conversations a ON a.owner=c.owner "
        "WHERE a.conversation IS NULL OR a.conversation!=c.id"
    )
    db.execute(
        "INSERT INTO summary_jobs(conversation,owner) SELECT DISTINCT conversation,owner "
        "FROM exchanges ORDER BY sequence"
    )


def policy(db: sqlite3.Connection) -> None:
    db.execute(
        "CREATE TABLE memory_policy (owner TEXT PRIMARY KEY REFERENCES owners(id), "
        "private INTEGER NOT NULL DEFAULT 0 CHECK(private IN (0,1)), "
        "epoch INTEGER NOT NULL DEFAULT 0)"
    )


def maintenance(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE summary_jobs ADD COLUMN last_served INTEGER NOT NULL DEFAULT 0")
    db.execute(
        "CREATE TABLE embedding_failures (chunk INTEGER NOT NULL REFERENCES chunks(id) "
        "ON DELETE CASCADE, model TEXT NOT NULL REFERENCES embedding_models(id), "
        "attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0, "
        "PRIMARY KEY(chunk,model))"
    )


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


def supersession(db: sqlite3.Connection) -> None:
    db.execute("ALTER TABLE memory_facts ADD COLUMN status_before_supersession TEXT")


def indexes_and_supersession_source(db: sqlite3.Connection) -> None:
    statements(db, INDEX_SCHEMA)
    columns = {row[1] for row in db.execute("PRAGMA table_info(detail_supersessions)")}
    if "source" not in columns:
        db.execute("ALTER TABLE detail_supersessions ADD COLUMN source TEXT")


MIGRATIONS = (
    Migration(1, "source archive", lambda db: statements(db, SOURCE_SCHEMA)),
    Migration(2, "source search and summaries", migrate_search),
    Migration(3, "versioned vectors", lambda db: statements(db, VECTOR_SCHEMA)),
    Migration(4, "privacy epoch", policy),
    Migration(5, "explicit details and provenance", lambda db: statements(db, DETAIL_SCHEMA)),
    Migration(6, "fact hierarchy and topic attention", lambda db: statements(db, HIERARCHY_SCHEMA)),
    Migration(7, "reversible supersession", supersession),
    Migration(8, "independent source visibility", repair_source_visibility),
    Migration(9, "fair summary work and recoverable vector failures", maintenance),
    Migration(
        10,
        "query indexes, FTS update trigger and detail supersession source",
        indexes_and_supersession_source,
    ),
)


def initialize(db: sqlite3.Connection, *, upgrade: bool, target: int = CURRENT_VERSION) -> None:
    version = int(db.execute("PRAGMA user_version").fetchone()[0])
    if version not in SUPPORTED_VERSIONS and not (upgrade and version == 0):
        raise ValueError(f"Unsupported Iris memory schema version {version}")
    if target not in SUPPORTED_VERSIONS or version > target:
        raise ValueError("Cannot downgrade an Iris memory archive")
    if not upgrade:
        return
    if not db.in_transaction:
        raise RuntimeError("Migrations require an explicit transaction")
    for migration in MIGRATIONS:
        if version < migration.version <= target:
            migration.apply(db)
            db.execute(f"PRAGMA user_version={migration.version}")
    if target >= 2:
        try:
            db.execute("SELECT rowid FROM chunks_fts LIMIT 0")
        except sqlite3.OperationalError as error:
            raise ValueError(
                "Conversation memory needs a working SQLite FTS5 index. "
                "Check your Python installation and archive."
            ) from error
