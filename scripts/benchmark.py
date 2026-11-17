"""Measure local turn orchestration and SQLite queries with synthetic data only.

This deliberately excludes model quality, inference, hardware and network latency.
Instrumentation wraps the existing calls; it does not change their ordering.
"""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import statistics
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import ExitStack, contextmanager
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iris_chat_engines.messages import Message  # noqa: E402
from iris_droid.protocol import write_wav  # noqa: E402
from iris_droid.server import STREAM_REPLY  # noqa: E402
from iris_memory import Exchange, SQLiteMemory  # noqa: E402
from iris_memory.context import MemoryContext  # noqa: E402
from iris_memory.hierarchy import Hierarchy  # noqa: E402
from iris_memory.index import MemoryIndex  # noqa: E402
from iris_settings import Settings  # noqa: E402
from tests.support import ScriptedBackend, make_harness  # noqa: E402


class SyntheticBackend(ScriptedBackend):
    def complete(self, messages, tools):
        system = messages[0].content or ""
        if system.startswith("Extract memory metadata"):
            reply = '{"facts":[],"topics":[]}'
        elif "original_exchanges" in (messages[-1].content or ""):
            reply = '{"notes":[]}'
        else:
            reply = "The synthetic greenhouse needs ventilation."
        self.replies.append(Message(role="assistant", content=reply))
        return super().complete(messages, tools)


def summarize(samples):
    ordered = sorted(samples)
    return {
        "calls": len(samples),
        "median_ms": round(statistics.median(samples), 3),
        "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
    }


def benchmark(exchanges: int, turns: int):
    with tempfile.TemporaryDirectory(prefix="iris-benchmark-") as directory:
        settings = Settings(
            base_url="http://model.invalid/v1",
            allow_remote=True,
            memory_enabled=True,
            memory_database=Path(directory) / "archive.sqlite3",
            memory_hierarchy_enabled=True,
            memory_summary_interval=8,
        )
        harness = make_harness(settings, SyntheticBackend())
        brain, archive = harness.brain, harness.brain.memory
        assert archive is not None
        try:
            conversation = archive.active_conversation("default")
            for number in range(exchanges):
                archive.append(
                    "default",
                    conversation,
                    Exchange(
                        f"seed-{number}",
                        "human",
                        f"Synthetic greenhouse ventilation project entry {number}.",
                        "The synthetic greenhouse needs ventilation.",
                    ),
                )
            modes = {}
            for mode in ("text", "buffered_voice", "streamed_voice"):
                stages = defaultdict(list)
                queries = Counter()
                connections = 0
                original_connect = SQLiteMemory.connect

                @contextmanager
                def connect(store, _connect=original_connect, _queries=queries, **kwargs):
                    nonlocal connections
                    connections += 1
                    with _connect(store, **kwargs) as db:
                        db.set_trace_callback(lambda sql: _queries.update([sql.split()[0].upper()]))
                        yield db

                def timed(name, operation, _stages=stages):
                    def invoke(*args, **kwargs):
                        started = perf_counter()
                        try:
                            return operation(*args, **kwargs)
                        finally:
                            _stages[name].append((perf_counter() - started) * 1000)

                    return invoke

                with ExitStack() as stack:
                    stack.enter_context(patch.object(SQLiteMemory, "connect", connect))
                    for owner, name, label in (
                        (brain, "_restore", "restore"),
                        (Hierarchy, "prepare", "hierarchy"),
                        (MemoryContext, "prepare", "memory_prepare_inclusive"),
                        (MemoryContext, "checkpoint", "summary_checkpoint"),
                        (harness.stt, "recognize", "fake_recognition"),
                        (harness.tts, "synthesize", "fake_synthesis"),
                        (archive, "append", "archive_commit"),
                        (harness.model, "complete", "fake_model"),
                    ):
                        stack.enter_context(
                            patch.object(owner, name, timed(label, getattr(owner, name)))
                        )
                    token = STREAM_REPLY.set(
                        (lambda reply, chunks, rate: sum(map(len, chunks)))
                        if mode == "streamed_voice"
                        else None
                    )
                    try:
                        for _ in range(turns):
                            harness.stt.text = "How is the greenhouse ventilation?"
                            body = (
                                harness.stt.text.encode()
                                if mode == "text"
                                else write_wav(b"\1\0" * 1600, 16000)
                            )
                            timed("whole_turn", brain.turn)(mode, body, text_only=mode == "text")
                    finally:
                        STREAM_REPLY.reset(token)
                modes[mode] = {
                    "stages": {name: summarize(times) for name, times in stages.items()},
                    "sqlite_connections": connections,
                    "sqlite_statements": dict(queries),
                }

            query_times = defaultdict(list)
            index = MemoryIndex(archive)
            for _ in range(turns):
                for name, call in (
                    ("recent", lambda: archive.recent("default", conversation, 12, 24000)),
                    ("search", lambda: index.search("default", conversation, "greenhouse", "")),
                    ("recall", lambda: index.recall_sources("default", ["greenhouse"], None, None)),
                ):
                    started = perf_counter()
                    call()
                    query_times[name].append((perf_counter() - started) * 1000)
            with archive.connect() as db:
                plans = {
                    "recent": [
                        row[3]
                        for row in db.execute(
                            "EXPLAIN QUERY PLAN SELECT * FROM exchanges "
                            "WHERE owner=? AND conversation=? "
                            "ORDER BY sequence DESC LIMIT 12",
                            ("default", conversation),
                        )
                    ],
                    "backlog": [
                        row[3]
                        for row in db.execute(
                            "EXPLAIN QUERY PLAN SELECT conversation FROM summary_jobs "
                            "WHERE owner=? AND attempts<3 ORDER BY rowid LIMIT 1",
                            ("default",),
                        )
                    ],
                }
            return {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "sqlite": sqlite3.sqlite_version,
                "seed_exchanges": exchanges,
                "turns_per_mode": turns,
                "note": "Synthetic serial orchestration only; stage times can be nested. "
                "Modes run sequentially on a growing archive. No inference or hardware timing. "
                "Statement counts include trigger/internal trace events. Summaries are empty.",
                "modes": modes,
                "queries": {k: summarize(v) for k, v in query_times.items()},
                "query_plans": plans,
            }
        finally:
            harness.transport.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchanges", type=int, default=1000)
    parser.add_argument("--turns", type=int, default=10)
    parser.add_argument("--output", type=Path, default=Path(".cache/checks/benchmark.json"))
    args = parser.parse_args()
    if not 1 <= args.exchanges <= 100000 or not 2 <= args.turns <= 1000:
        parser.error("Use 1–100000 exchanges and 2–1000 turns")
    result = benchmark(args.exchanges, args.turns)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Synthetic benchmark saved to {args.output}")


if __name__ == "__main__":
    main()
