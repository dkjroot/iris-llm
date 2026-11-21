"""Versioned SQLite vectors, bounded resumable indexing and filtered local search."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from datetime import datetime
from typing import TYPE_CHECKING, cast

import numpy as np

from iris_memory.embeddings import EmbeddingError, Encoder
from iris_memory.records import SearchHit
from iris_memory.schema import VECTOR_SCHEMA
from iris_memory.text import is_recall_request, topical_terms
from iris_turn import check_deadline, measured

if TYPE_CHECKING:
    from iris_memory import SQLiteMemory

SCHEMA = VECTOR_SCHEMA


LOGGER = logging.getLogger(__name__)
# Candidate policy is intentionally unchanged; date filters run before this horizon.
SEMANTIC_CANDIDATE_LIMIT = 4096


class SemanticMemory:
    def __init__(self, archive: SQLiteMemory, encoder: Encoder, minimum: float = 0.45) -> None:
        self.archive = archive
        self.encoder = encoder
        self.minimum = minimum
        with archive.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO embedding_models VALUES (?,?)",
                (encoder.model_id, encoder.dimensions),
            )
            row = db.execute(
                "SELECT dimensions FROM embedding_models WHERE id=?", (encoder.model_id,)
            ).fetchone()
            if row[0] != encoder.dimensions:
                raise EmbeddingError(
                    "Stored embedding dimensions don't match the selected model version"
                )

    @measured("indexing")
    def index_batch(self, owner: str, limit: int = 8) -> int:

        if not 1 <= limit <= 32:
            raise ValueError("Embedding batches must have 1–32 chunks")
        with self.archive.snapshot() as db:
            policy = self.archive.policy(owner)
            if policy.private:
                return 0
            rows = list(
                db.execute(
                    "SELECT c.*,substr(e.user_text,1,600) AS user_context, "
                    "substr(e.assistant_text,1,600) AS assistant_context,e.user_text "
                    "FROM chunks c JOIN exchanges e ON e.id=c.exchange "
                    "WHERE c.owner=? AND c.superseded=0 AND e.origin='human' "
                    "AND NOT EXISTS(SELECT 1 FROM embedding_vectors v "
                    "WHERE v.chunk=c.id AND v.model=?) "
                    "AND NOT EXISTS(SELECT 1 FROM embedding_failures f WHERE f.chunk=c.id "
                    "AND f.model=? AND (f.attempts>=3 OR f.retry_at>?)) "
                    "ORDER BY c.id LIMIT ?",
                    (owner, self.encoder.model_id, self.encoder.model_id, time.time(), limit),
                )
            )
        if not rows:
            return 0
        try:
            check_deadline()
            encoded = self._encode_rows(rows)
            check_deadline()
        except EmbeddingError:
            if len(rows) > 1:
                # Isolate a single bad chunk rather than penalizing innocent neighbors.
                encoded = {}
                failed_rows = []
                for row in rows:
                    check_deadline()
                    try:
                        encoded.update(self._encode_rows([row]))
                    except EmbeddingError:
                        failed_rows.append(row)
                if failed_rows:
                    with self.archive.connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        if self.archive._policy(db, owner) == policy:
                            for row in failed_rows:
                                db.execute(
                                    "INSERT INTO embedding_failures(chunk,model,attempts,retry_at) "
                                    "SELECT id,?,1,? FROM chunks WHERE id=? AND owner=? "
                                    "AND exchange=? AND text=? "
                                    "ON CONFLICT(chunk,model) DO UPDATE SET "
                                    "attempts=embedding_failures.attempts+1,"
                                    "retry_at=excluded.retry_at",
                                    (
                                        self.encoder.model_id,
                                        time.time() + 60,
                                        row["id"],
                                        owner,
                                        row["exchange"],
                                        row["text"],
                                    ),
                                )
                if not encoded:
                    raise
                rows = [row for row in rows if row["id"] in encoded]
            else:
                with self.archive.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if self.archive._policy(db, owner) == policy:
                        for row in rows:
                            db.execute(
                                "INSERT INTO embedding_failures(chunk,model,attempts,retry_at) "
                                "SELECT id,?,1,? FROM chunks WHERE id=? AND owner=? "
                                "AND exchange=? AND text=? "
                                "ON CONFLICT(chunk,model) DO UPDATE SET "
                                "attempts=embedding_failures.attempts+1,retry_at=excluded.retry_at",
                                (
                                    self.encoder.model_id,
                                    time.time() + 60,
                                    row["id"],
                                    owner,
                                    row["exchange"],
                                    row["text"],
                                ),
                            )
                raise
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self.archive._policy(db, owner) != policy:
                return 0
            committed = 0
            for row in rows:
                # Check stable source ID AND text after inference, not just reusable
                # chunk rowid. Stale work cannot recreate deleted/rebuilt sources.
                inserted = db.execute(
                    "INSERT OR IGNORE INTO embedding_vectors(chunk,model,text_hash,vector) "
                    "SELECT id,?,?,? FROM chunks WHERE id=? AND owner=? AND exchange=? "
                    "AND text=? AND superseded=0",
                    (
                        self.encoder.model_id,
                        hashlib.sha256(row["text"].encode()).hexdigest(),
                        encoded.get(row["id"], b""),
                        row["id"],
                        owner,
                        row["exchange"],
                        row["text"],
                    ),
                )
                committed += inserted.rowcount
                if inserted.rowcount:
                    db.execute(
                        "DELETE FROM embedding_failures WHERE chunk=? AND model=?",
                        (row["id"], self.encoder.model_id),
                    )
        return committed

    def _encode_rows(self, rows: list[sqlite3.Row]) -> dict[int, bytes]:
        # Empty vectors mark non-evidence turns as processed without exposing them
        # to semantic search. Missing rows remain a recoverable durable work queue.
        useful = [
            row
            for row in rows
            if not is_recall_request(row["user_text"]) and topical_terms(row["user_text"])
        ]
        # Embed the content with its paired reply/question, rather than the
        # provenance labels. Labels stay in retrieved evidence, not model input.
        documents = []
        for row in useful:
            fragment = row["text"].split(": ", 1)[-1]
            documents.append(
                fragment + " " + row["assistant_context"]
                if row["text"].startswith("Human")
                else row["user_context"] + " " + fragment
            )
        vectors = self.encoder.encode(documents)
        if (
            vectors.shape != (len(useful), self.encoder.dimensions)
            or not np.isfinite(vectors).all()
        ):
            raise EmbeddingError("Invalid embedding batch dimensions or values")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if (norms <= 0).any():
            raise EmbeddingError("Embedding model returned a zero vector")
        vectors = np.asarray(vectors / np.maximum(norms, 1e-12), dtype=np.float32)
        encoded = {
            row["id"]: np.asarray(vector, dtype="<f4").tobytes()
            for row, vector in zip(useful, vectors, strict=True)
        }
        return encoded

    def pending(self, owner: str) -> int:
        with self.archive.connect() as db:
            return int(
                db.execute(
                    "SELECT count(*) FROM chunks c JOIN exchanges e ON e.id=c.exchange "
                    "WHERE c.owner=? AND e.origin='human' "
                    "AND c.superseded=0 AND NOT EXISTS "
                    "(SELECT 1 FROM embedding_vectors v WHERE v.chunk=c.id AND v.model=?)",
                    (owner, self.encoder.model_id),
                ).fetchone()[0]
            )

    def reset(self, owner: str) -> None:
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.archive._bump_policy(db, owner)
            db.execute(
                "DELETE FROM embedding_failures WHERE model=? AND chunk IN "
                "(SELECT id FROM chunks WHERE owner=?)",
                (self.encoder.model_id, owner),
            )
            db.execute(
                "DELETE FROM embedding_vectors WHERE model=? AND chunk IN "
                "(SELECT id FROM chunks WHERE owner=?)",
                (self.encoder.model_id, owner),
            )

    @measured("retrieval")
    def search(
        self,
        owner: str,
        query: str,
        *,
        active: str | None = None,
        explicit: bool = False,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 6,
    ) -> list[SearchHit]:
        if not query.strip():
            return []
        start_text, end_text = (
            start.isoformat() if start else None,
            end.isoformat() if end else None,
        )
        # Select bounded candidate IDs before fetching vector/text payloads, then
        # score all valid candidate vectors in one vectorized BLAS matrix multiply.
        with self.archive.connect() as db:
            rows = list(
                db.execute(
                    "SELECT c.id,c.exchange,c.owner,c.conversation,c.text,e.created_at,"
                    "substr(e.user_text,1,8000) AS user_text,e.sequence,v.vector "
                    "FROM (SELECT c.id FROM embedding_vectors v "
                    "JOIN chunks c ON c.id=v.chunk JOIN exchanges e ON e.id=c.exchange "
                    "WHERE v.model=? AND c.owner=? AND c.superseded=0 AND length(v.vector)>0 "
                    "AND (? IS NULL OR julianday(e.created_at)>=julianday(?)) "
                    "AND (? IS NULL OR julianday(e.created_at)<julianday(?)) "
                    "AND (? OR c.conversation=? OR NOT EXISTS(SELECT 1 FROM recall_blocks b "
                    "WHERE b.owner=c.owner AND b.conversation=c.conversation)) "
                    "ORDER BY e.sequence DESC,c.id DESC LIMIT ?) chosen "
                    "JOIN chunks c ON c.id=chosen.id "
                    "JOIN exchanges e ON e.id=c.exchange "
                    "JOIN embedding_vectors v ON v.chunk=c.id AND v.model=? "
                    "ORDER BY e.sequence DESC,c.id DESC",
                    (
                        self.encoder.model_id,
                        owner,
                        start_text,
                        start_text,
                        end_text,
                        end_text,
                        explicit,
                        active,
                        SEMANTIC_CANDIDATE_LIMIT,
                        self.encoder.model_id,
                    ),
                )
            )
        if not rows:
            return []
        query_vector = self.encoder.encode([query[:8000]])
        if (
            query_vector.shape != (1, self.encoder.dimensions)
            or not np.isfinite(query_vector).all()
        ):
            raise EmbeddingError("Invalid query embedding")
        norm = float(np.linalg.norm(query_vector))
        if norm <= 0:
            raise EmbeddingError("Embedding model returned a zero query vector")
        query_vector = (query_vector / norm).astype(np.float32, copy=False)
        expected_bytes = self.encoder.dimensions * 4
        valid_rows: list[sqlite3.Row] = []
        blobs: list[bytes] = []
        for row in rows:
            raw = row["vector"]
            if len(raw) == expected_bytes:
                valid_rows.append(row)
                blobs.append(raw)
        if not blobs:
            return []
        matrix = np.frombuffer(b"".join(blobs), dtype="<f4").reshape(
            len(blobs), self.encoder.dimensions
        )
        norms = np.linalg.norm(matrix, axis=1)
        finite_unit = np.isfinite(matrix).all(axis=1) & (np.abs(norms - 1.0) <= 0.001)
        if not finite_unit.any():
            return []
        keep_indices = np.flatnonzero(finite_unit)
        sims = np.clip(matrix[keep_indices] @ query_vector[0], -1.0, 1.0)
        above = np.flatnonzero(sims >= self.minimum)
        if not above.size:
            return []
        candidates = [
            (
                float(sims[idx]),
                int(valid_rows[int(keep_indices[idx])]["sequence"]),
                int(keep_indices[idx]),
            )
            for idx in above
        ]
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        result: list[SearchHit] = []
        seen: set[str] = set()
        for similarity, _, row_idx in candidates:
            row = valid_rows[row_idx]
            if row["exchange"] in seen:
                continue
            item = cast(SearchHit, {key: row[key] for key in row.keys() if key != "vector"})
            item["semantic_similarity"] = similarity
            result.append(item)
            seen.add(row["exchange"])
            if len(result) >= limit:
                break
        return result
