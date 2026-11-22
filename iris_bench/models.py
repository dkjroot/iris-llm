"""Strict Pydantic v2 schemas for Iris Bench configs, probes, transcripts and public results."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictBenchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BenchLocalConfig(StrictBenchModel):
    """Local gitignored benchmark preparation state."""

    snapshot_path: str
    probes_path: str
    lamp_device: str | None = None
    judge_model: str = "google/gemini-3.8-flash"
    prepared_at_utc: str
    snapshot_counts: dict[str, int] = Field(default_factory=dict)


class MemoryProbe(StrictBenchModel):
    """One local memory probe evaluated against the snapshotted database."""

    id: str = Field(min_length=1, max_length=64)
    kind: Literal["detail", "fact", "note", "search"]
    prompt: str = Field(min_length=1, max_length=1000)
    followup_prompt: str | None = Field(default=None, max_length=1000)
    expected_any: list[str] = Field(default_factory=list, max_length=20)
    forbidden_any: list[str] = Field(default_factory=list, max_length=20)
    expected_source_conversations: list[str] = Field(default_factory=list, max_length=10)
    require_search_tool: bool = False
    judge_safe: bool = False
    sanitized_reference: str = ""
    source_table: str = ""
    source_id: str = ""


class MemoryProbeFile(StrictBenchModel):
    """Gitignored local probe suite drafted by prepare and reviewed by the owner."""

    version: str = "1"
    owner: str = "default"
    created_at_utc: str
    instructions: str
    probes: list[MemoryProbe] = Field(default_factory=list)


class HardwareInfo(StrictBenchModel):
    """Coarse hardware profile safe to publish (never hostnames, usernames or paths)."""

    os: str
    arch: str
    python: str
    cpu: str | None = None
    ram_gb: float | None = None
    gpu_name: str | None = None
    gpu_vram_gb: float | None = None


class LLMCallTelemetry(StrictBenchModel):
    """Raw timing and token statistics captured from one ChatCompletionsBackend call."""

    streamed: bool
    ttft_ms: float | None = None
    first_content_ms: float | None = None
    total_model_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tokens_per_sec: float | None = None
    est_tokens_per_sec: float | None = None
    prompt_tokens_per_sec: float | None = None
    output_chars: int = 0
    finish_reason: str | None = None
    tool_calls_count: int = 0
    raw_content: str = ""


class ToolCallRecord(StrictBenchModel):
    """Tool execution trace stored only in the private local transcript."""

    name: str
    arguments: str
    result_summary_ok: bool
    duration_ms: float
    raw_result: str


class TurnRecord(StrictBenchModel):
    """Private per-turn transcript & telemetry kept under .cache/iris-bench/runs/."""

    case_id: str
    category: str
    turn_index: int
    prompt: str
    raw_reply: str = ""
    spoken_text: str = ""
    expression: str | None = None
    streamed_sentences: list[str] = Field(default_factory=list)
    ttft_ms: float | None = None
    ttfs_ms: float | None = None
    model_ms: float | None = None
    tool_ms: float = 0.0
    turn_ms: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tokens_per_sec: float | None = None
    est_tokens_per_sec: float | None = None
    prompt_tokens_per_sec: float | None = None
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    memory_sources: list[str] = Field(default_factory=list)
    independent_device_state: str | None = None
    judge_safe: bool = True
    judge_rubric: str = ""
    error: str | None = None


class CheckResult(StrictBenchModel):
    """Sanitized pass/fail check suitable for public JSON results."""

    id: str
    category: str
    name: str
    passed: bool
    weight: float = 1.0
    reason: str = ""


class CategoryScore(StrictBenchModel):
    """Aggregated score for one benchmark category."""

    skipped: bool = False
    passed: int = 0
    total: int = 0
    score: float = 0.0
    checks: list[CheckResult] = Field(default_factory=list)


class SpeedSummary(StrictBenchModel):
    """Voice-pipeline latency and throughput statistics across metered interactive turns."""

    metered_turns: int = 0
    median_ttft_ms: float | None = None
    p95_ttft_ms: float | None = None
    median_ttfs_ms: float | None = None
    p95_ttfs_ms: float | None = None
    median_model_ms: float | None = None
    median_turn_ms: float | None = None
    median_tok_per_sec: float | None = None
    median_est_tok_per_sec: float | None = None
    median_prompt_tok_per_sec: float | None = None
    long_conversation_ttfs_ms: list[float | None] = Field(default_factory=list)
    long_conversation_model_ms: list[float | None] = Field(default_factory=list)


class JudgeScoreSummary(StrictBenchModel):
    """Numeric-only summary of OpenRouter LLM-as-a-judge evaluation."""

    judge_model: str
    evaluated_at_utc: str
    sample_count: int
    excluded_private_count: int
    overall_score: float
    dimensions: dict[str, float] = Field(default_factory=dict)
    case_scores: dict[str, float] = Field(default_factory=dict)


class ScoreSummary(StrictBenchModel):
    """Headline composite and sub-scores."""

    overall_iris_score: float
    capability_score: float
    voice_responsiveness_score: float
    judge_quality_score: float | None = None
    weights: dict[str, float] = Field(default_factory=dict)


class ConfigSummary(StrictBenchModel):
    """Non-private run settings recorded for apples-to-apples comparisons."""

    temperature: float
    max_tokens: int
    model_context_tokens: int
    tools_enabled: bool
    mock_tools: bool
    warmup_turns: int
    runs: int
    memory_mode: Literal["snapshot", "synthetic", "none"]
    memory_probe_count: int
    has_semantic_memory: bool
    has_home_service: bool
    has_weather: bool


class PublicBenchmarkResult(StrictBenchModel):
    """GitHub-safe public benchmark result JSON."""

    schema_version: str = "1.0"
    suite_version: str
    run_id: str
    timestamp_utc: str
    iris_version: str
    iris_git_commit: str | None = None
    engine: str
    model: str
    model_file: str | None = None
    benchmark_label: str | None = None
    comments: str = ""
    hardware: HardwareInfo
    config: ConfigSummary
    scores: ScoreSummary
    speed: SpeedSummary
    categories: dict[str, CategoryScore]
    judge: JudgeScoreSummary | None = None
    warnings: list[str] = Field(default_factory=list)


class PrivateRunTranscript(StrictBenchModel):
    """Local gitignored transcript used for debugging and privacy-gated judging."""

    run_id: str
    public_result_path: str
    engine: str
    model: str
    timestamp_utc: str
    weather_location_to_scrub: str = ""
    lamp_device_to_scrub: str = ""
    turns: list[TurnRecord] = Field(default_factory=list)
    judge_notes: dict[str, str] = Field(default_factory=dict)
