"""Compare lexical and hybrid recall on an explicit local synthetic corpus."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel, Field

from iris_memory import Exchange, SQLiteMemory
from iris_memory.embeddings import MiniLMEncoder
from iris_memory.index import MemoryIndex
from iris_memory.recall import RecallResolver
from iris_memory.semantic import SemanticMemory


class Source(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    conversation: str = Field(min_length=1, max_length=100)
    at: datetime
    user: str = Field(max_length=8000)
    assistant: str = Field(max_length=8000)


class Query(BaseModel):
    utterance: str = Field(max_length=1000)
    outcome: str
    sources: list[str] = Field(max_length=3)
    semantic_sources: list[str] | None = Field(default=None, max_length=3)
    known_model_limit: bool = False


class Corpus(BaseModel):
    now: datetime
    exchanges: list[Source] = Field(max_length=100)
    queries: list[Query] = Field(max_length=100)


def evaluate(directory: Path, corpus_path: Path, minimum: float = 0.45) -> dict[str, Any]:
    if corpus_path.stat().st_size > 2000000:
        raise ValueError("Evaluation corpus exceeds 2 MB")
    corpus = Corpus.model_validate_json(corpus_path.read_bytes())
    if corpus.now.tzinfo is None or any(row.at.tzinfo is None for row in corpus.exchanges):
        raise ValueError("Evaluation timestamps must include timezone offsets")
    started = time.perf_counter()
    encoder = MiniLMEncoder(directory)
    load_ms = (time.perf_counter() - started) * 1000
    with TemporaryDirectory(prefix="iris-recall-evaluation-") as temporary:
        archive = SQLiteMemory(Path(temporary) / "evaluation.sqlite3")
        labels: dict[str, str] = {}
        for source in corpus.exchanges:
            if source.conversation not in labels:
                labels[source.conversation] = archive.start_conversation("evaluation")
            archive.append(
                "evaluation",
                labels[source.conversation],
                Exchange(source.id, "human", source.user, source.assistant),
            )
            with archive.connect() as db:
                db.execute(
                    "UPDATE exchanges SET created_at=? WHERE id=?",
                    (source.at.isoformat(), source.id),
                )
                db.execute(
                    "UPDATE chunks SET created_at=? WHERE exchange=?",
                    (source.at.isoformat(), source.id),
                )
        active = archive.start_conversation("evaluation")
        index = MemoryIndex(archive)
        resolver = RecallResolver(index, "Europe/London", clock=lambda: corpus.now)
        semantic = SemanticMemory(archive, encoder, minimum)
        started = time.perf_counter()
        while semantic.pending("evaluation"):
            semantic.index_batch("evaluation", 32)
        index_ms = (time.perf_counter() - started) * 1000
        modes: dict[str, Any] = {}
        for mode in ("lexical", "hybrid"):
            index.semantic = semantic if mode == "hybrid" else None
            cases = []
            started = time.perf_counter()
            for query in corpus.queries:
                result = resolver.resolve("evaluation", active, query.utterance, [])
                found = {row["exchange"] for row in result.excerpts}
                if result.pending:
                    found.update(source for c in result.pending.candidates for source in c.sources)
                expected = set(
                    query.semantic_sources if query.semantic_sources is not None else query.sources
                )
                cases.append(
                    {
                        "query": query.utterance,
                        "known_model_limit": query.known_model_limit,
                        "expected": sorted(expected),
                        "found": sorted(found),
                        "evidence_match": found == expected,
                        "confirmation_required": bool(
                            result.pending
                            and result.pending.selected is None
                            and result.pending.candidates
                        ),
                    }
                )
            modes[mode] = {
                "evidence_matches": sum(row["evidence_match"] for row in cases),
                "total": len(cases),
                "total_ms": round((time.perf_counter() - started) * 1000, 2),
                "cases": cases,
            }
        return {
            "model_id": encoder.model_id,
            "minimum_similarity": minimum,
            "model_load_ms": round(load_ms, 2),
            "index_ms": round(index_ms, 2),
            "modes": modes,
            "note": (
                "Synthetic retrieval evidence checks; "
                "not calibrated confidence or a hardware benchmark."
            ),
        }
