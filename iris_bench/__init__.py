"""Iris Bench: benchmark local LLM speed and Iris capabilities without leaking private data."""

from __future__ import annotations

from pathlib import Path

SUITE_VERSION = "1.0"
DEFAULT_JUDGE_MODEL = "google/gemini-3.8-flash"
BENCH_CACHE_DIR = Path(".cache/iris-bench")
DEFAULT_RESULTS_DIR = Path("benchmarks/results")
DEFAULT_REPORT_HTML = Path("benchmarks/report.html")

__all__ = [
    "BENCH_CACHE_DIR",
    "DEFAULT_JUDGE_MODEL",
    "DEFAULT_REPORT_HTML",
    "DEFAULT_RESULTS_DIR",
    "SUITE_VERSION",
]
