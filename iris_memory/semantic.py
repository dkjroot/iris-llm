"""Versioned SQLite vectors, bounded resumable indexing and filtered local search."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from iris_memory.embeddings import EmbeddingError, Encoder

if TYPE_CHECKING:
    from iris_memory import SQLiteMemory

LOGGER = logging.getLogger(__name__)
SCHEMA = (
    "CREATE TABLE embedding_models (id TEXT PRIMARY KEY, dimensions INTEGER NOT NULL)",
    "CREATE TABLE embedding_vectors (chunk INTEGER NOT NULL REFERENCES chunks(id) "
    "ON DELETE CASCADE, "
    "model TEXT NOT NULL REFERENCES embedding_models(id), "
    "text_hash TEXT NOT NULL, vector BLOB NOT NULL, "
    "PRIMARY KEY(chunk,model))",
    "PRAGMA user_version=3",
)


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

    def index_batch(self, owner: str, limit: int = 8) -> int:
        from iris_memory.recall import is_recall_request, topical_terms

        if not 1 <= limit <= 32:
            raise ValueError("Embedding batches must have 1–32 chunks")
        with self.archive.connect() as db:
            rows = list(
                db.execute(
                    "SELECT c.*,substr(e.user_text,1,600) AS user_context, "
                    "substr(e.assistant_text,1,600) AS assistant_context,e.user_text "
                    "FROM chunks c JOIN exchanges e ON e.id=c.exchange "
                    "WHERE c.owner=? AND c.superseded=0 AND e.origin='human' "
                    "AND NOT EXISTS(SELECT 1 FROM embedding_vectors v "
                    "WHERE v.chunk=c.id AND v.model=?) "
                    "ORDER BY c.id LIMIT ?",
                    (owner, self.encoder.model_id, limit),
                )
            )
        if not rows:
            return 0
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
        with self.archive.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for row in rows:
                # Check stable source ID AND text after inference, not just reusable
                # chunk rowid. Stale work cannot recreate deleted/rebuilt sources.
                db.execute(
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
        return len(rows)

    def pending(self, owner: str) -> int:
        with self.archive.connect() as db:
            return int(
                db.execute(
                    "SELECT count(*) FROM chunks c WHERE c.owner=? "
                    "AND c.superseded=0 AND NOT EXISTS "
                    "(SELECT 1 FROM embedding_vectors v WHERE v.chunk=c.id AND v.model=?)",
                    (owner, self.encoder.model_id),
                ).fetchone()[0]
            )

    def reset(self, owner: str) -> None:
        with self.archive.connect() as db:
            db.execute(
                "DELETE FROM embedding_vectors WHERE model=? AND chunk IN "
                "(SELECT id FROM chunks WHERE owner=?)",
                (self.encoder.model_id, owner),
            )

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
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        start_text, end_text = (
            start.isoformat() if start else None,
            end.isoformat() if end else None,
        )
        with self.archive.connect() as db:
            rows = list(
                db.execute(
                    "SELECT c.id,c.exchange,c.owner,c.conversation,c.text,e.created_at,"
                    "substr(e.user_text,1,8000) AS user_text,e.sequence,v.vector "
                    "FROM embedding_vectors v "
                    "JOIN chunks c ON c.id=v.chunk JOIN exchanges e ON e.id=c.exchange "
                    "WHERE v.model=? AND c.owner=? AND c.superseded=0 AND length(v.vector)>0 "
                    "AND (? IS NULL OR julianday(e.created_at)>=julianday(?)) "
                    "AND (? IS NULL OR julianday(e.created_at)<julianday(?)) "
                    "AND (? OR c.conversation=? OR NOT EXISTS(SELECT 1 FROM recall_blocks b "
                    "WHERE b.owner=c.owner AND b.conversation=c.conversation)) "
                    "ORDER BY e.sequence DESC,c.id DESC LIMIT 4096",
                    (
                        self.encoder.model_id,
                        owner,
                        start_text,
                        start_text,
                        end_text,
                        end_text,
                        explicit,
                        active,
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
        query_vector = query_vector / norm
        scored = []
        for row in rows:
            if len(row["vector"]) != self.encoder.dimensions * 4:
                continue
            vector = np.frombuffer(row["vector"], dtype="<f4")
            if vector.shape != (self.encoder.dimensions,) or not np.isfinite(vector).all():
                continue
            if abs(float(np.linalg.norm(vector)) - 1.0) > 0.001:
                continue
            similarity = min(1.0, max(-1.0, float(vector @ query_vector[0])))
            if similarity >= self.minimum:
                item = {key: row[key] for key in row.keys() if key != "vector"}
                item["semantic_similarity"] = similarity
                scored.append(item)
        scored.sort(key=lambda item: (item["semantic_similarity"], item["sequence"]), reverse=True)
        result = []
        seen = set()
        for item in scored:
            if item["exchange"] not in seen:
                result.append(item)
                seen.add(item["exchange"])
                if len(result) >= limit:
                    break
        return result
