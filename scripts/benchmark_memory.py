"""Measure synthetic archive reads and their actual SQLite query plans.

No real archive, model or network is used. Vector timings exclude real inference.
The corpus deliberately puts one paraphrased topic beyond the semantic horizon.
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import sys
import tempfile
import tracemalloc
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iris_memory import SQLiteMemory  # noqa: E402
from iris_memory.index import MemoryIndex, index_exchange  # noqa: E402
from iris_memory.semantic import SemanticMemory  # noqa: E402
from scripts.benchmark import summarize  # noqa: E402


class SyntheticEncoder:
    model_id = "benchmark-only:three-dimensions"
    dimensions = 3

    def encode(self, texts):
        return np.asarray(
            [[1, 0, 0] if "rainwater" in t or "collection" in t else [0, 1, 0] for t in texts],
            dtype=np.float32,
        ).reshape((-1, 3))


def benchmark(exchanges, repetitions):
    with tempfile.TemporaryDirectory(prefix="iris-memory-benchmark-") as directory:
        archive = SQLiteMemory(Path(directory) / "archive.sqlite3")
        conversation = archive.start_conversation("synthetic")
        with archive.connect() as db:
            db.executemany(
                "INSERT INTO exchanges(id,owner,conversation,created_at,origin,"
                "user_text,assistant_text,tool_names) VALUES (?,'synthetic',?,?,'human',?,?,'[]')",
                (
                    (
                        str(number),
                        conversation,
                        "2000-01-01T00:00:00+00:00" if number == 0 else "2026-01-01T00:00:00+00:00",
                        "rainwater greenhouse" if number == 0 else f"cinema project {number}",
                        "Synthetic reply",
                    )
                    for number in range(exchanges)
                ),
            )
            for row in db.execute("SELECT * FROM exchanges ORDER BY sequence"):
                index_exchange(db, row)
        index = MemoryIndex(archive)
        index.enqueue("synthetic", conversation)
        semantic = SemanticMemory(archive, SyntheticEncoder())
        while semantic.pending("synthetic"):
            assert semantic.index_batch("synthetic", 32)
        operations = {
            "recent": lambda: archive.recent("synthetic", conversation, 12, 24000),
            "summary_snapshot": lambda: index.summary_snapshot("synthetic", conversation),
            "lexical_old_topic": lambda: index.recall_sources(
                "synthetic", ["rainwater", "greenhouse"], None, None
            ),
            "semantic_undated": lambda: semantic.search("synthetic", "collection", explicit=True),
            "semantic_dated": lambda: semantic.search(
                "synthetic", "collection", explicit=True, end=datetime(2001, 1, 1, tzinfo=UTC)
            ),
        }
        results = {}
        plans = defaultdict(list)
        for name, operation in operations.items():
            statements = set()
            original_connect = archive.connect

            @contextmanager
            def connect(_connect=original_connect, _statements=statements, **kwargs):
                with _connect(**kwargs) as db:
                    db.set_trace_callback(_statements.add)
                    yield db

            with patch.object(archive, "connect", connect):
                result = operation()
            samples = []
            for _ in range(repetitions):
                started = perf_counter()
                operation()
                samples.append((perf_counter() - started) * 1000)
            tracemalloc.start()
            operation()
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            results[name] = {**summarize(samples), "peak_python_bytes": peak}
            if name in ("lexical_old_topic", "semantic_dated", "semantic_undated"):
                results[name]["found_old_topic"] = any(r["exchange"] == "0" for r in result)
            with archive.connect() as db:
                for statement in sorted(statements):
                    if statement.lstrip().upper().startswith("SELECT"):
                        plans[name].append(
                            [r[3] for r in db.execute("EXPLAIN QUERY PLAN " + statement)]
                        )
        return {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sqlite": sqlite3.sqlite_version,
            "exchanges": exchanges,
            "chunks": exchanges * 2,
            "archive_bytes": archive.path.stat().st_size,
            "note": "Synthetic warmed reads; no real inference. Memory is Python allocations, "
            "not SQLite/native RSS. Query plans come from the executed SELECT statements.",
            "reads": results,
            "query_plans": dict(plans),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchanges", type=int, default=10000)
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path(".cache/checks/memory-benchmark.json"))
    args = parser.parse_args()
    if not 1 <= args.exchanges <= 100000 or not 2 <= args.repetitions <= 1000:
        parser.error("Use 1–100000 exchanges and 2–1000 repetitions")
    result = benchmark(args.exchanges, args.repetitions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Synthetic memory benchmark saved to {args.output}")


if __name__ == "__main__":
    main()
