"""SQLite derived data and a bounded, restartable serial summarisation queue."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from iris_memory.embeddings import EmbeddingError
from iris_memory.models import Summary

if TYPE_CHECKING:
    from iris_memory import SQLiteMemory
    from iris_memory.semantic import SemanticMemory


LOGGER = logging.getLogger(__name__)

SCHEMA = (
    "CREATE TABLE summaries (id TEXT PRIMARY KEY, owner TEXT NOT NULL, conversation TEXT NOT NULL, "
    "through_sequence INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
    "payload TEXT NOT NULL, FOREIGN KEY(owner,conversation) REFERENCES conversations(owner,id) "
    "ON DELETE CASCADE, UNIQUE(conversation,through_sequence))",
    "CREATE TABLE notes (id TEXT PRIMARY KEY, summary TEXT NOT NULL REFERENCES summaries(id) "
    "ON DELETE CASCADE, owner TEXT NOT NULL, conversation TEXT NOT NULL, text TEXT NOT NULL, "
    "kind TEXT NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL, sources TEXT NOT NULL)",
    "CREATE INDEX conversation_notes ON notes(owner,conversation)",
    "CREATE TABLE summary_jobs (conversation TEXT PRIMARY KEY, owner TEXT NOT NULL, "
    "attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0, "
    "FOREIGN KEY(owner,conversation) REFERENCES conversations(owner,id) ON DELETE CASCADE)",
    "CREATE TABLE recall_blocks (owner TEXT NOT NULL, conversation TEXT PRIMARY KEY "
    "REFERENCES conversations(id) ON DELETE CASCADE)",
    "CREATE TABLE chunks (id INTEGER PRIMARY KEY, exchange TEXT NOT NULL REFERENCES exchanges(id) "
    "ON DELETE CASCADE, owner TEXT NOT NULL, conversation TEXT NOT NULL, created_at TEXT NOT NULL, "
    "text TEXT NOT NULL, superseded INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX chunk_sources ON chunks(exchange)",
    "CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='id')",
    "CREATE TRIGGER chunks_insert AFTER INSERT ON chunks BEGIN "
    "INSERT INTO chunks_fts(rowid,text) VALUES (new.id,new.text); END",
    "CREATE TRIGGER chunks_delete AFTER DELETE ON chunks BEGIN "
    "INSERT INTO chunks_fts(chunks_fts,rowid,text) VALUES ('delete',old.id,old.text); END",
    "PRAGMA user_version=2",
)


def index_exchange(db: sqlite3.Connection, row: sqlite3.Row) -> None:
    if row["origin"] != "human":
        return
    # Each chunk carries its speaker, even when a long exchange is split.
    for speaker, field in (
        ("Human (recognised/typed, unverified)", "user_text"),
        ("Iris (assistant response, not human testimony)", "assistant_text"),
    ):
        text = row[field]
        for start in range(0, len(text), 1000):
            db.execute(
                "INSERT INTO chunks(exchange,owner,conversation,created_at,text) "
                "VALUES (?,?,?,?,?)",
                (
                    row["id"],
                    row["owner"],
                    row["conversation"],
                    row["created_at"],
                    speaker + ": " + text[start : start + 1200],
                ),
            )


def migrate(db: sqlite3.Connection) -> None:
    try:
        db.execute("CREATE VIRTUAL TABLE temp.iris_fts_probe USING fts5(text)")
        db.execute("DROP TABLE temp.iris_fts_probe")
    except sqlite3.OperationalError as error:
        raise ValueError(
            "Conversation memory needs SQLite FTS5. Install Python with FTS5 support."
        ) from error
    for statement in SCHEMA:
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
        "FROM exchanges ORDER BY sequence DESC LIMIT 64"
    )


STOP_WORDS = set(
    "the and that this with from have what about were your would could should "
    "there then they them into just know tell please remember discussed talked "
    "earlier yesterday other some more when where which again next time now".split()
)


def query_terms(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            w.lower() for w in re.findall(r"[^\W_]{3,40}", text) if w.lower() not in STOP_WORDS
        )
    )[:16]


class MemoryIndex:
    def __init__(self, archive: SQLiteMemory) -> None:
        self.archive = archive
        self.semantic: SemanticMemory | None = None

    def enqueue(self, owner: str, conversation: str) -> None:
        with self.archive.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO summary_jobs(conversation,owner) "
                "SELECT id,owner FROM conversations c WHERE owner=? AND id=? "
                "AND EXISTS (SELECT 1 FROM exchanges e WHERE e.conversation=c.id "
                "AND e.sequence>COALESCE((SELECT MAX(through_sequence) FROM summaries "
                "WHERE conversation=c.id),0)) AND (SELECT count(*) FROM summary_jobs)<64",
                (owner, conversation),
            )

    def next_conversation(self, owner: str, exclude: str) -> str | None:
        with self.archive.connect() as db:
            row = db.execute(
                "SELECT conversation FROM summary_jobs WHERE owner=? AND conversation!=? "
                "AND attempts<3 AND retry_at<=? ORDER BY rowid LIMIT 1",
                (owner, exclude, time.time()),
            ).fetchone()
            return str(row[0]) if row else None

    def rebuild(self, owner: str) -> None:
        """Explicit offline rebuild of derived data, retaining sources and clear boundaries."""
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM summaries WHERE owner=?", (owner,))
            db.execute("DELETE FROM chunks WHERE owner=?", (owner,))
            db.execute("DELETE FROM summary_jobs WHERE owner=?", (owner,))
            for row in db.execute(
                "SELECT * FROM exchanges WHERE owner=? ORDER BY sequence", (owner,)
            ):
                index_exchange(db, row)
            db.execute(
                "INSERT INTO summary_jobs(conversation,owner) SELECT conversation,owner "
                "FROM exchanges WHERE owner=? GROUP BY conversation "
                "ORDER BY MAX(sequence) DESC LIMIT MAX(0,64-(SELECT count(*) FROM summary_jobs))",
                (owner,),
            )

    def retry(self, owner: str) -> None:
        with self.archive.connect() as db:
            db.execute("UPDATE summary_jobs SET attempts=0,retry_at=0 WHERE owner=?", (owner,))

    def pending(self, owner: str, conversation: str) -> dict[str, Any] | None:
        with self.archive.connect() as db:
            row = db.execute(
                "SELECT j.*, COALESCE(MAX(s.through_sequence),0) AS cursor "
                "FROM summary_jobs j LEFT JOIN summaries s ON s.conversation=j.conversation "
                "WHERE j.owner=? AND j.conversation=? AND j.attempts<3 AND j.retry_at<=? "
                "GROUP BY j.conversation",
                (owner, conversation, time.time()),
            ).fetchone()
            if row is None:
                return None
            job = dict(row)
            job["pending_count"] = db.execute(
                "SELECT count(*) FROM (SELECT sequence FROM exchanges WHERE owner=? "
                "AND conversation=? AND sequence>? LIMIT 100)",
                (owner, conversation, job["cursor"]),
            ).fetchone()[0]
            return job

    def sources(
        self, owner: str, conversation: str, after: int, limit: int = 12
    ) -> list[dict[str, Any]]:
        with self.archive.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT sequence,id,created_at,origin,user_text,assistant_text FROM exchanges "
                    "WHERE owner=? AND conversation=? AND sequence>? ORDER BY sequence LIMIT ?",
                    (owner, conversation, after, limit),
                )
            ]

    def notes(self, owner: str, conversation: str, limit: int = 24) -> list[dict[str, Any]]:
        with self.archive.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT n.*,s.through_sequence,s.created_at FROM notes n JOIN summaries s "
                    "ON s.id=n.summary WHERE n.owner=? AND n.conversation=? "
                    "AND n.status!='superseded' "
                    "ORDER BY s.through_sequence DESC,n.rowid DESC LIMIT ?",
                    (owner, conversation, limit),
                )
            ]

    def save(
        self,
        owner: str,
        conversation: str,
        revision: str,
        cursor: int,
        through: int,
        summary: Summary,
        *,
        expected_epoch: int | None = None,
    ) -> bool:
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            private, epoch = self.archive._policy(db, owner)
            if private or (expected_epoch is not None and epoch != expected_epoch):
                return False
            latest = db.execute(
                "SELECT id FROM exchanges WHERE owner=? AND conversation=? "
                "ORDER BY sequence DESC LIMIT 1",
                (owner, conversation),
            ).fetchone()
            current = db.execute(
                "SELECT COALESCE(MAX(through_sequence),0) FROM summaries "
                "WHERE owner=? AND conversation=?",
                (owner, conversation),
            ).fetchone()[0]
            if not latest or latest[0] != revision or current != cursor:
                return False
            sid = str(uuid4())
            db.execute(
                "INSERT INTO summaries(id,owner,conversation,through_sequence,payload) "
                "VALUES (?,?,?,?,?)",
                (sid, owner, conversation, through, summary.model_dump_json()),
            )
            for number, note in enumerate(summary.notes):
                for old in note.supersedes:
                    previous = db.execute(
                        "SELECT sources FROM notes WHERE id=? AND owner=? AND conversation=?",
                        (old, owner, conversation),
                    ).fetchone()
                    if previous:
                        db.execute("UPDATE notes SET status='superseded' WHERE id=?", (old,))
                        for source in json.loads(previous[0]):
                            db.execute("UPDATE chunks SET superseded=1 WHERE exchange=?", (source,))
                db.execute(
                    "INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        f"{sid}:{number}",
                        sid,
                        owner,
                        conversation,
                        note.text,
                        note.kind,
                        note.scope,
                        note.status,
                        json.dumps(note.sources),
                    ),
                )
            db.execute(
                "UPDATE summary_jobs SET attempts=0,retry_at=0 WHERE owner=? AND conversation=?",
                (owner, conversation),
            )
            remaining = db.execute(
                "SELECT 1 FROM exchanges WHERE owner=? AND conversation=? AND sequence>? LIMIT 1",
                (owner, conversation, through),
            ).fetchone()
            if not remaining:
                db.execute(
                    "DELETE FROM summary_jobs WHERE owner=? AND conversation=?",
                    (owner, conversation),
                )
            return True

    def failed(self, owner: str, conversation: str) -> None:
        with self.archive.connect() as db:
            db.execute(
                "UPDATE summary_jobs SET attempts=attempts+1,retry_at=? "
                "WHERE owner=? AND conversation=?",
                (time.time() + 60, owner, conversation),
            )

    def search(
        self, owner: str, active: str, utterance: str, recent: str, *, limit: int = 3
    ) -> list[dict[str, Any]]:
        terms = query_terms(utterance)
        context = [w for w in query_terms(recent) if w not in terms][:4]
        terms = terms[:12] + context
        if not terms:
            return []
        # Quote only tokenizer-generated words: no user-supplied FTS operators.
        query = " OR ".join('"' + word + '"' for word in terms)
        explicit = bool(
            re.search(
                r"\b(remember|recall|resume|discussed|talked|yesterday)\b|last time|other day",
                utterance,
                re.I,
            )
        )
        with self.archive.connect() as db:
            rows = db.execute(
                "SELECT c.*,e.sequence FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
                "JOIN exchanges e ON e.id=c.exchange WHERE chunks_fts MATCH ? AND c.owner=? "
                "AND c.superseded=0 AND (? OR c.conversation=?) "
                "AND (? OR NOT EXISTS (SELECT 1 FROM recall_blocks b WHERE b.owner=c.owner "
                "AND b.conversation=c.conversation)) "
                "ORDER BY bm25(chunks_fts),e.sequence DESC LIMIT ?",
                (
                    query,
                    owner,
                    explicit or bool(query_terms(utterance)),
                    active,
                    explicit,
                    limit * 4,
                ),
            )
            result = []
            seen = set()
            for row in rows:
                if row["exchange"] not in seen:
                    seen.add(row["exchange"])
                    result.append(dict(row))
                    if len(result) >= limit:
                        break
        if self.semantic is not None:
            try:
                self.semantic.index_batch(owner)
                for row in self.semantic.search(
                    owner, " ".join(terms), active=active, explicit=explicit
                ):
                    existing = next(
                        (item for item in result if item["exchange"] == row["exchange"]), None
                    )
                    if existing is not None:
                        existing["semantic_similarity"] = row["semantic_similarity"]
                    else:
                        result.append(row)
                        seen.add(row["exchange"])
                result.sort(
                    key=lambda item: (
                        len(set(terms) & set(query_terms(item["text"])))
                        + 2 * item.get("semantic_similarity", 0),
                        item["created_at"],
                    ),
                    reverse=True,
                )
            except (EmbeddingError, sqlite3.Error) as error:
                LOGGER.warning(
                    "Semantic search unavailable (%s); using keyword matches.", type(error).__name__
                )
        return result[:limit]

    def recall_sources(
        self,
        owner: str,
        terms: list[str],
        start: datetime | None,
        end: datetime | None,
    ) -> list[dict[str, Any]]:
        """Bounded explicit recall: search notes, then verify their original sources."""
        from iris_memory.recall import is_recall_request, topical_terms

        # julianday compares instants even if old/imported timestamps use another
        # UTC offset. Both local-midnight boundaries have already been resolved.
        dates = (start.isoformat() if start else None, end.isoformat() if end else None)
        filters = (
            "e.owner=? AND e.origin='human' "
            "AND (? IS NULL OR julianday(e.created_at)>=julianday(?)) "
            "AND (? IS NULL OR julianday(e.created_at)<julianday(?)) "
            "AND EXISTS(SELECT 1 FROM chunks c WHERE c.exchange=e.id AND c.superseded=0)"
        )
        args = (owner, dates[0], dates[0], dates[1], dates[1])
        result: dict[str, dict[str, Any]] = {}
        with self.archive.connect() as db:
            note_filter = " OR ".join("n.text LIKE ?" for _ in terms) or "1"
            notes = db.execute(
                "SELECT n.* FROM notes n JOIN summaries s ON s.id=n.summary "
                "WHERE n.owner=? AND n.status!='superseded' AND (" + note_filter + ") "
                "ORDER BY s.through_sequence DESC LIMIT 12",
                (owner, *(f"%{t}%" for t in terms)),
            )
            for note in notes:
                sources = json.loads(note["sources"])[:6]
                for row in db.execute(
                    "SELECT e.id,e.id AS exchange,e.owner,e.conversation,"
                    "e.sequence,e.created_at,e.user_text "
                    "FROM exchanges e WHERE "
                    + filters
                    + " AND e.conversation=? AND e.id IN ("
                    + ",".join("?" for _ in sources)
                    + ") "
                    "ORDER BY e.sequence DESC",
                    (*args, note["conversation"], *sources),
                ):
                    if not is_recall_request(row["user_text"]) and topical_terms(row["user_text"]):
                        result[row["id"]] = {**dict(row), "topic": note["text"]}
                if len(result) >= 24:
                    break
            if terms:
                query = " OR ".join('"' + word + '"' for word in terms[:16])
                rows = db.execute(
                    "SELECT e.id,e.id AS exchange,e.owner,e.conversation,"
                    "e.sequence,e.created_at,e.user_text "
                    "FROM chunks_fts JOIN chunks c "
                    "ON c.id=chunks_fts.rowid JOIN exchanges e ON e.id=c.exchange "
                    "WHERE chunks_fts MATCH ? AND c.superseded=0 AND "
                    + filters
                    + " ORDER BY bm25(chunks_fts),e.sequence DESC LIMIT 96",
                    (query, *args),
                )
            else:
                # A date alone may identify more than one discussion. Don't pick
                # the last writer as an implicit answer to a vague spoken request.
                rows = db.execute(
                    "SELECT e.id,e.id AS exchange,e.owner,e.conversation,"
                    "e.sequence,e.created_at,e.user_text "
                    "FROM exchanges e WHERE " + filters + " ORDER BY e.sequence DESC LIMIT 48",
                    args,
                )
            for row in rows:
                if not is_recall_request(row["user_text"]) and topical_terms(row["user_text"]):
                    result.setdefault(row["id"], dict(row))
                if len(result) >= 36:
                    break
        if self.semantic is not None and terms:
            try:
                self.semantic.index_batch(owner)
                for row in self.semantic.search(
                    owner, " ".join(terms), explicit=True, start=start, end=end
                ):
                    if row["exchange"] in result:
                        result[row["exchange"]]["semantic_similarity"] = row["semantic_similarity"]
                    else:
                        row["semantic_only"] = True
                        result[row["exchange"]] = row
            except (EmbeddingError, sqlite3.Error) as error:
                LOGGER.warning(
                    "Semantic recall unavailable (%s); using keyword matches.", type(error).__name__
                )
        return list(result.values())[:42]

    def recall_evidence(
        self, owner: str, conversation: str, sources: tuple[str, ...], terms: tuple[str, ...] = ()
    ) -> list[dict[str, Any]]:
        def excerpt(text: str) -> str:
            match = (
                re.search("|".join(re.escape(term) for term in terms), text, re.I)
                if terms
                else None
            )
            start = max(0, match.start() - 150) if match else 0
            end = min(len(text), start + 500)
            return ("… " if start else "") + text[start:end] + (" …" if end < len(text) else "")

        with self.archive.connect() as db:
            rows = db.execute(
                "SELECT e.* FROM exchanges e WHERE e.owner=? AND e.conversation=? "
                "AND e.id IN (" + ",".join("?" for _ in sources[:3]) + ") "
                "AND EXISTS(SELECT 1 FROM chunks c WHERE c.exchange=e.id AND c.superseded=0) "
                "ORDER BY e.sequence",
                (owner, conversation, *sources[:3]),
            )
            return [
                {
                    "exchange": row["id"],
                    "conversation": row["conversation"],
                    "created_at": row["created_at"],
                    "text": "Human (recognised/typed, unverified): "
                    + excerpt(row["user_text"])
                    + "\nIris (historical assistant response): "
                    + excerpt(row["assistant_text"]),
                }
                for row in rows
            ]
