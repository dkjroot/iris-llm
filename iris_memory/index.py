"""SQLite derived data and durable summary jobs processed in bounded serial steps."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from iris_memory.embeddings import EmbeddingError
from iris_memory.maintenance import enqueue_summary, yield_summary
from iris_memory.migrations import repair_source_visibility as repair_source_visibility
from iris_memory.models import Summary
from iris_memory.records import (
    NoteRecord,
    RecallSource,
    SearchHit,
    Source,
    SourceExcerpt,
    SummaryJob,
    SummarySnapshot,
)
from iris_memory.schema import SEARCH_SCHEMA

if TYPE_CHECKING:
    from iris_memory import SQLiteMemory
    from iris_memory.hierarchy import Hierarchy
    from iris_memory.semantic import SemanticMemory


SCHEMA = SEARCH_SCHEMA


LOGGER = logging.getLogger(__name__)


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
        self.hierarchy: Hierarchy | None = None

    def enqueue(self, owner: str, conversation: str) -> None:
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM exchanges e WHERE e.owner=? AND e.conversation=? "
                "AND e.sequence>COALESCE((SELECT MAX(through_sequence) FROM summaries "
                "WHERE conversation=e.conversation),0) LIMIT 1",
                (owner, conversation),
            ).fetchone():
                enqueue_summary(db, owner, conversation)

    def next_conversation(self, owner: str, exclude: str) -> str | None:
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            modern = db.execute("PRAGMA user_version").fetchone()[0] >= 9
            # Recover legacy omissions. Newly discovered jobs join behind already
            # queued work; rowid provides deterministic ordering within the batch.
            db.execute(
                "INSERT OR IGNORE INTO summary_jobs(conversation,owner"
                + (
                    ",last_served) SELECT c.id,c.owner,"
                    "(SELECT COALESCE(MAX(last_served),0) FROM summary_jobs)+c.rowid "
                    if modern
                    else ") SELECT c.id,c.owner "
                )
                + "FROM conversations c WHERE c.owner=? AND c.id!=? "
                "AND NOT EXISTS(SELECT 1 FROM summary_jobs j WHERE j.conversation=c.id) "
                "AND EXISTS(SELECT 1 FROM exchanges e WHERE e.conversation=c.id "
                "AND e.sequence>COALESCE((SELECT MAX(through_sequence) FROM summaries "
                "WHERE conversation=c.id),0)) ORDER BY c.rowid",
                (owner, exclude),
            )
            row = db.execute(
                "SELECT conversation FROM summary_jobs WHERE owner=? AND conversation!=? "
                "AND attempts<3 AND retry_at<=? ORDER BY "
                + ("last_served," if modern else "")
                + "rowid LIMIT 1",
                (owner, exclude, time.time()),
            ).fetchone()
            return str(row[0]) if row else None

    def rebuild(self, owner: str) -> None:
        """Explicit offline rebuild of derived data, retaining sources and clear boundaries."""
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.archive._bump_policy(db, owner)
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
                "ORDER BY MIN(sequence)",
                (owner,),
            )

    def retry(self, owner: str) -> None:
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE summary_jobs SET attempts=0,retry_at=0 WHERE owner=?", (owner,))
            if db.execute("PRAGMA user_version").fetchone()[0] >= 9:
                db.execute(
                    "DELETE FROM embedding_failures WHERE chunk IN "
                    "(SELECT id FROM chunks WHERE owner=?)",
                    (owner,),
                )

    def pending(self, owner: str, conversation: str) -> SummaryJob | None:
        with self.archive.snapshot() as db:
            row = db.execute(
                "SELECT j.*, COALESCE(MAX(s.through_sequence),0) AS cursor "
                "FROM summary_jobs j LEFT JOIN summaries s ON s.conversation=j.conversation "
                "WHERE j.owner=? AND j.conversation=? AND j.attempts<3 AND j.retry_at<=? "
                "GROUP BY j.conversation",
                (owner, conversation, time.time()),
            ).fetchone()
            if row is None:
                return None
            job = cast(SummaryJob, dict(row))
            job["pending_count"] = db.execute(
                "SELECT count(*) FROM (SELECT sequence FROM exchanges WHERE owner=? "
                "AND conversation=? AND sequence>? LIMIT 100)",
                (owner, conversation, job["cursor"]),
            ).fetchone()[0]
            return job

    def sources(self, owner: str, conversation: str, after: int, limit: int = 12) -> list[Source]:
        with self.archive.connect() as db:
            return [
                cast(Source, dict(r))
                for r in db.execute(
                    "SELECT sequence,id,created_at,origin,user_text,assistant_text FROM exchanges "
                    "WHERE owner=? AND conversation=? AND sequence>? ORDER BY sequence LIMIT ?",
                    (owner, conversation, after, limit),
                )
            ]

    def notes(self, owner: str, conversation: str, limit: int = 24) -> list[NoteRecord]:
        with self.archive.connect() as db:
            return [
                cast(NoteRecord, dict(r))
                for r in db.execute(
                    "SELECT n.*,s.through_sequence,s.created_at FROM notes n JOIN summaries s "
                    "ON s.id=n.summary WHERE n.owner=? AND n.conversation=? "
                    "AND n.status!='superseded' "
                    "ORDER BY s.through_sequence DESC,n.rowid DESC LIMIT ?",
                    (owner, conversation, limit),
                )
            ]

    def summary_snapshot(self, owner: str, conversation: str) -> SummarySnapshot:
        with self.archive.snapshot():
            policy = self.archive.policy(owner)
            if policy.private:
                return SummarySnapshot(policy, None, None, (), ())
            job = self.pending(owner, conversation)
            return SummarySnapshot(
                policy,
                self.archive.revision(owner, conversation),
                job,
                tuple(self.sources(owner, conversation, job["cursor"])) if job else (),
                tuple(self.notes(owner, conversation, 12)) if job else (),
            )

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
            affected_sources: set[str] = set()
            for number, note in enumerate(summary.notes):
                for old in note.supersedes:
                    previous = db.execute(
                        "SELECT sources FROM notes WHERE id=? AND owner=? AND conversation=?",
                        (old, owner, conversation),
                    ).fetchone()
                    if previous:
                        db.execute("UPDATE notes SET status='superseded' WHERE id=?", (old,))
                        affected_sources.update(json.loads(previous[0]))
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
            # A source can support several independent notes. Retire it only
            # when none of those claims remains current, after all replacements
            # have been inserted. Original exchanges are never changed here.
            if affected_sources:
                current_sources: set[str] = set()
                for row in db.execute(
                    "SELECT sources FROM notes WHERE owner=? AND conversation=? "
                    "AND status!='superseded'",
                    (owner, conversation),
                ):
                    current_sources.update(affected_sources.intersection(json.loads(row[0])))
                db.executemany(
                    "UPDATE chunks SET superseded=? WHERE owner=? AND conversation=? "
                    "AND exchange=?",
                    (
                        (source not in current_sources, owner, conversation, source)
                        for source in affected_sources
                    ),
                )
            db.execute(
                "UPDATE summary_jobs SET attempts=0,retry_at=0 WHERE owner=? AND conversation=?",
                (owner, conversation),
            )
            yield_summary(db, owner, conversation)
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
            db.execute("BEGIN IMMEDIATE")
            yield_summary(db, owner, conversation)
            db.execute(
                "UPDATE summary_jobs SET attempts=attempts+1,retry_at=? "
                "WHERE owner=? AND conversation=?",
                (time.time() + 60, owner, conversation),
            )

    def search(
        self, owner: str, active: str, utterance: str, recent: str, *, limit: int = 3
    ) -> list[SearchHit]:
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
                    result.append(cast(SearchHit, dict(row)))
                    if len(result) >= (limit * 4 if self.hierarchy is not None else limit):
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
        if self.hierarchy is not None:
            result = self.hierarchy.rank_sources(owner, utterance, result)
        return result[:limit]

    def recall_sources(
        self,
        owner: str,
        terms: list[str],
        start: datetime | None,
        end: datetime | None,
    ) -> list[RecallSource]:
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
        result: dict[str, RecallSource] = {}
        with self.archive.snapshot() as db:
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
                        result[row["id"]] = cast(RecallSource, {**dict(row), "topic": note["text"]})
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
                    result.setdefault(row["id"], cast(RecallSource, dict(row)))
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
                        candidate = cast(RecallSource, dict(row))
                        candidate["semantic_only"] = True
                        result[row["exchange"]] = candidate
            except (EmbeddingError, sqlite3.Error) as error:
                LOGGER.warning(
                    "Semantic recall unavailable (%s); using keyword matches.", type(error).__name__
                )
        candidates = list(result.values())[:42]
        if self.hierarchy is not None:
            activity = self.hierarchy.activity_scores(owner, " ".join(terms), candidates)
            for row in candidates:
                # Attention only breaks close ordering ties; it never supplies lexical
                # confidence or bypasses date/source/ambiguity checks.
                row["hierarchy_activity"] = min(0.5, activity.get(row["exchange"], 0) / 10)
        return candidates

    def recall_evidence(
        self, owner: str, conversation: str, sources: tuple[str, ...], terms: tuple[str, ...] = ()
    ) -> list[SourceExcerpt]:
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


def migrate(db: sqlite3.Connection) -> None:
    """Compatibility entry for the original search migration."""
    from iris_memory.migrations import migrate_search

    migrate_search(db)
