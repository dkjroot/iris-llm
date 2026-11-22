"""Deterministic category scoring, voice responsiveness curves and composite Iris Score."""

from __future__ import annotations

import statistics

from iris_bench.models import (
    CategoryScore,
    CheckResult,
    PublicBenchmarkResult,
    ScoreSummary,
    SpeedSummary,
    TurnRecord,
)

CATEGORY_WEIGHTS: dict[str, float] = {
    "conversation": 1.0,
    "long_conversation": 1.2,
    "expressions": 1.0,
    "tools_weather_time": 1.0,
    "tools_home": 1.3,
    "personality": 0.9,
    "memory_recall": 1.5,
    "memory_extraction": 1.1,
}


def _percentile95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return round(ordered[idx], 2)


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


def summarize_speed(turns: list[TurnRecord]) -> SpeedSummary:
    """Compute speed summary across metered interactive turns (excluding warm-ups)."""
    interactive = [t for t in turns if t.category != "memory_extraction" and t.error is None]
    ttfts = [t.ttft_ms for t in interactive if t.ttft_ms is not None]
    ttfss = [t.ttfs_ms for t in interactive if t.ttfs_ms is not None]
    models = [t.model_ms for t in interactive if t.model_ms is not None]
    whole_turns = [t.turn_ms for t in interactive if t.turn_ms > 0]
    tps = [t.tokens_per_sec for t in interactive if t.tokens_per_sec is not None]
    est_tps = [t.est_tokens_per_sec for t in interactive if t.est_tokens_per_sec is not None]
    ptps = [t.prompt_tokens_per_sec for t in interactive if t.prompt_tokens_per_sec is not None]

    long_turns = sorted(
        (t for t in interactive if t.category == "long_conversation"),
        key=lambda t: t.turn_index,
    )
    return SpeedSummary(
        metered_turns=len(interactive),
        median_ttft_ms=_median(ttfts),
        p95_ttft_ms=_percentile95(ttfts),
        median_ttfs_ms=_median(ttfss),
        p95_ttfs_ms=_percentile95(ttfss),
        median_model_ms=_median(models),
        median_turn_ms=_median(whole_turns),
        median_tok_per_sec=_median(tps),
        median_est_tok_per_sec=_median(est_tps),
        median_prompt_tok_per_sec=_median(ptps),
        long_conversation_ttfs_ms=[t.ttfs_ms for t in long_turns],
        long_conversation_model_ms=[t.model_ms for t in long_turns],
    )


def compute_category_score(checks: list[CheckResult], *, skipped: bool = False) -> CategoryScore:
    """Aggregate checks inside one category."""
    if skipped or not checks:
        return CategoryScore(skipped=skipped, passed=0, total=len(checks), score=0.0, checks=checks)
    total_weight = sum(c.weight for c in checks)
    passed_weight = sum(c.weight for c in checks if c.passed)
    passed_count = sum(1 for c in checks if c.passed)
    score = round(100.0 * passed_weight / total_weight, 1) if total_weight > 0 else 0.0
    return CategoryScore(
        skipped=False,
        passed=passed_count,
        total=len(checks),
        score=score,
        checks=checks,
    )


def compute_capability_score(categories: dict[str, CategoryScore]) -> float:
    """Weighted average capability score across all non-skipped categories."""
    weighted_sum = 0.0
    weight_total = 0.0
    for name, cat in categories.items():
        if cat.skipped or cat.total == 0:
            continue
        weight = CATEGORY_WEIGHTS.get(name, 1.0)
        weighted_sum += cat.score * weight
        weight_total += weight
    if weight_total <= 0:
        return 0.0
    return round(weighted_sum / weight_total, 1)


def compute_voice_responsiveness_score(speed: SpeedSummary) -> float:
    """Score conversational voice responsiveness from latency (TTFS) and throughput (tok/s)."""
    latency_ms = speed.median_ttfs_ms
    if latency_ms is None:
        latency_ms = speed.median_ttft_ms
    if latency_ms is None:
        latency_ms = speed.median_model_ms

    tps = speed.median_tok_per_sec
    if tps is None:
        tps = speed.median_est_tok_per_sec

    latency_score: float | None = None
    if latency_ms is not None:
        latency_score = max(0.0, min(100.0, 100.0 * (1.0 - (latency_ms - 600.0) / 5400.0)))

    tps_score: float | None = None
    if tps is not None:
        tps_score = max(0.0, min(100.0, 100.0 * (tps - 5.0) / 55.0))

    if latency_score is not None and tps_score is not None:
        return round(0.70 * latency_score + 0.30 * tps_score, 1)
    if latency_score is not None:
        return round(latency_score, 1)
    if tps_score is not None:
        return round(tps_score, 1)
    return 0.0


def build_scores(
    categories: dict[str, CategoryScore],
    speed: SpeedSummary,
    judge_quality: float | None = None,
) -> ScoreSummary:
    """Compute composite and headline sub-scores."""
    capability = compute_capability_score(categories)
    voice_resp = compute_voice_responsiveness_score(speed)
    if judge_quality is not None:
        weights = {"capability": 0.50, "judge_quality": 0.25, "voice_responsiveness": 0.25}
        overall = round(
            0.50 * capability + 0.25 * judge_quality + 0.25 * voice_resp,
            1,
        )
    else:
        weights = {"capability": 0.75, "voice_responsiveness": 0.25}
        overall = round(0.75 * capability + 0.25 * voice_resp, 1)
    return ScoreSummary(
        overall_iris_score=overall,
        capability_score=capability,
        voice_responsiveness_score=voice_resp,
        judge_quality_score=round(judge_quality, 1) if judge_quality is not None else None,
        weights=weights,
    )


def recompute_scores(result: PublicBenchmarkResult) -> PublicBenchmarkResult:
    """Return a copy of *result* with recomputed capability, responsiveness and overall scores."""
    judge_score = result.judge.overall_score if result.judge is not None else None
    new_scores = build_scores(result.categories, result.speed, judge_score)
    return result.model_copy(update={"scores": new_scores})
