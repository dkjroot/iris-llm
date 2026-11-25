"""Prepare read-only DB snapshots and execute unattended Iris Bench runs."""

from __future__ import annotations

import importlib.metadata
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import SecretStr

from iris_app.runtime import Brain
from iris_bench import BENCH_CACHE_DIR, DEFAULT_JUDGE_MODEL, DEFAULT_RESULTS_DIR, SUITE_VERSION
from iris_bench.hardware import detect_hardware
from iris_bench.models import (
    BenchLocalConfig,
    CategoryScore,
    CheckResult,
    ConfigSummary,
    MemoryProbe,
    MemoryProbeFile,
    PrivateRunTranscript,
    PublicBenchmarkResult,
    TurnRecord,
)
from iris_bench.scenarios import (
    BenchRuntimeHandles,
    run_conversation_category,
    run_expressions_category,
    run_home_category,
    run_long_conversation_category,
    run_memory_extraction_category,
    run_memory_recall_category,
    run_personality_category,
    run_warmups,
    run_weather_time_category,
)
from iris_bench.scoring import build_scores, compute_category_score, summarize_speed
from iris_bench.snapshot import (
    create_synthetic_memory_db,
    draft_memory_probes,
    load_dotenv_if_present,
    snapshot_database,
    working_db_copy,
)
from iris_bench.telemetry import (
    BenchMockHomeClient,
    BenchStreamingTTS,
    BenchSTT,
    MeteredChatCompletionsBackend,
    MockWeatherTransport,
    TurnTelemetryCollector,
    instrument_tool_registry,
)
from iris_chat_engines.backends import ChatBackend
from iris_chat_engines.compatible import ChatEngine
from iris_memory import database_path
from iris_memory.embeddings import model_directory
from iris_memory.embeddings import verify as verify_embeddings
from iris_paths import PROJECT_ROOT, atomic_json, server_config
from iris_settings import Settings, load_settings


@dataclass(frozen=True)
class PrepareSummary:
    config: BenchLocalConfig
    probe_count: int
    probes_reused: bool


@dataclass(frozen=True)
class RunOutcome:
    result: PublicBenchmarkResult
    public_path: Path
    transcript: PrivateRunTranscript
    transcript_path: Path


def _atomic_json_retry(path: Path, data: object) -> None:
    """Retry atomic_json briefly on transient Windows AV/indexer file locks."""
    for attempt in range(5):
        try:
            atomic_json(path, data)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02 * (attempt + 1))


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
    return (cleaned or "unknown")[:48].lower()


def _iris_version_and_commit() -> tuple[str, str | None]:
    try:
        version = importlib.metadata.version("iris-llm")
    except importlib.metadata.PackageNotFoundError:
        version = "0.2.0"
    commit: str | None = None
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            commit = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return version, commit


def _load_bench_settings(
    config_path: Path | None,
    *,
    dotenv: dict[str, str],
) -> tuple[Settings, Path | None]:
    resolved_cfg = server_config(config_path)
    settings = load_settings(resolved_cfg)
    updates: dict[str, object] = {}
    if settings.home_service_token is None and dotenv.get("IRIS_HOME_SERVICE_TOKEN"):
        updates["home_service_token"] = SecretStr(dotenv["IRIS_HOME_SERVICE_TOKEN"])
    if settings.api_key is None and dotenv.get("IRIS_API_KEY"):
        updates["api_key"] = SecretStr(dotenv["IRIS_API_KEY"])
    if updates:
        settings = settings.model_copy(update=updates)
    return settings, resolved_cfg


def prepare_benchmark(
    *,
    config_path: Path | None = None,
    db_path: Path | None = None,
    lamp_device: str | None = None,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    cache_dir: Path = BENCH_CACHE_DIR,
    force_probes: bool = False,
) -> PrepareSummary:
    """Snapshot the live Iris SQLite DB read-only and draft gitignored memory probes."""
    cache_dir = cache_dir.expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    dotenv = load_dotenv_if_present(PROJECT_ROOT)
    settings, _ = _load_bench_settings(config_path, dotenv=dotenv)

    source_db = (db_path or database_path(settings)).expanduser().resolve()
    snapshot_path = cache_dir / "snapshot.sqlite3"
    counts = snapshot_database(source_db, snapshot_path)

    probes_path = cache_dir / "memory_probes.json"
    probes_reused = probes_path.is_file() and not force_probes
    if probes_reused:
        probe_file = MemoryProbeFile.model_validate_json(probes_path.read_text(encoding="utf-8"))
    else:
        probe_file = draft_memory_probes(snapshot_path, owner=settings.memory_owner)
        atomic_json(probes_path, probe_file.model_dump(mode="json"))

    local_cfg = BenchLocalConfig(
        snapshot_path=str(snapshot_path),
        probes_path=str(probes_path),
        lamp_device=lamp_device,
        judge_model=judge_model,
        prepared_at_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        snapshot_counts=counts,
    )
    atomic_json(cache_dir / "config.json", local_cfg.model_dump(mode="json"))
    return PrepareSummary(
        config=local_cfg,
        probe_count=len(probe_file.probes),
        probes_reused=probes_reused,
    )


def load_local_bench_config(cache_dir: Path = BENCH_CACHE_DIR) -> BenchLocalConfig | None:
    cfg_path = cache_dir.expanduser().resolve() / "config.json"
    if not cfg_path.is_file():
        return None
    return BenchLocalConfig.model_validate_json(cfg_path.read_text(encoding="utf-8"))


def assert_public_privacy(
    result: PublicBenchmarkResult,
    *,
    forbidden_terms: list[str],
) -> None:
    """Verify no private probes, town names, device names or secrets appear in public JSON."""
    serialized = result.model_dump_json().casefold()
    for term in forbidden_terms:
        cleaned = term.strip()
        if len(cleaned) < 5:
            continue
        if cleaned.casefold() in serialized:
            raise ValueError(
                "Privacy check failed: a private term matched the public benchmark JSON."
            )


def _merge_categories(runs_categories: list[dict[str, CategoryScore]]) -> dict[str, CategoryScore]:
    if not runs_categories:
        return {}
    if len(runs_categories) == 1:
        return runs_categories[0]
    merged: dict[str, CategoryScore] = {}
    all_names = list(dict.fromkeys(name for rc in runs_categories for name in rc))
    for name in all_names:
        cats = [rc[name] for rc in runs_categories if name in rc]
        if all(c.skipped for c in cats):
            merged[name] = compute_category_score([], skipped=True)
            continue
        checks: list[CheckResult] = []
        for idx, cat in enumerate(cats, start=1):
            for chk in cat.checks:
                checks.append(
                    chk.model_copy(
                        update={
                            "id": f"r{idx}_{chk.id}",
                            "name": f"[Run {idx}] {chk.name}",
                        }
                    )
                )
        merged[name] = compute_category_score(checks, skipped=False)
    return merged


def _run_single_pass(
    run_idx: int,
    *,
    run_settings: Settings,
    transport: httpx.BaseTransport,
    probes: list[MemoryProbe],
    effective_lamp: str | None,
    mock_tools: bool,
    warmup_turns: int,
    on_category_done: Callable[[str, CategoryScore, list[TurnRecord], str | None], None]
    | None = None,
) -> tuple[dict[str, CategoryScore], list[TurnRecord], list[str]]:
    collector = TurnTelemetryCollector()
    stt = BenchSTT()
    tts = BenchStreamingTTS(collector)

    def make_metered_backend(cfg: Settings, http_client: httpx.Client) -> ChatBackend:
        return MeteredChatCompletionsBackend(cfg, http_client, collector)

    turns: list[TurnRecord] = []
    cat_scores: dict[str, CategoryScore] = {}
    warnings: list[str] = []
    with httpx.Client(transport=transport, trust_env=False) as http_client:
        brain = Brain(
            run_settings,
            http_client,
            stt,
            tts,
            backend_factory=make_metered_backend,
        )
        try:
            # Stop background maintenance worker so it never contends with metered turns.
            if brain.memory_worker is not None:
                brain.memory_worker.close(timeout=2.0)
                brain.memory_worker = None

            original_new_chat: Callable[[], ChatEngine] = brain._new_chat

            def metered_new_chat() -> ChatEngine:
                chat = original_new_chat()
                instrument_tool_registry(chat.tools, collector)
                return chat

            brain._new_chat = metered_new_chat  # type: ignore[method-assign]

            active_home: BenchMockHomeClient | None = None
            if mock_tools:
                active_home = BenchMockHomeClient(effective_lamp or "living room lamp")
                brain.tool_services.home = active_home  # type: ignore[assignment]

            home_client = (
                active_home
                if active_home is not None
                else (brain.tool_services.home if run_settings.home_service_url else None)
            )
            handles = BenchRuntimeHandles(
                brain=brain,
                stt=stt,
                collector=collector,
                home_client=home_client,
                lamp_device=effective_lamp,
                weather_enabled=run_settings.weather_enabled,
                mock_tools=mock_tools,
                weather_location=run_settings.weather_location,
            )

            run_warmups(handles, warmup_turns, run_idx)

            runners: list[tuple[str, Callable[[], tuple[CategoryScore, list[TurnRecord]]]]] = [
                ("conversation", lambda: run_conversation_category(handles, run_idx)),
                ("long_conversation", lambda: run_long_conversation_category(handles, run_idx)),
                ("expressions", lambda: run_expressions_category(handles, run_idx)),
                ("tools_weather_time", lambda: run_weather_time_category(handles, run_idx)),
                ("tools_home", lambda: run_home_category(handles, run_idx)),
                ("personality", lambda: run_personality_category(handles, run_idx)),
                ("memory_recall", lambda: run_memory_recall_category(handles, probes, run_idx)),
                ("memory_extraction", lambda: run_memory_extraction_category(handles)),
            ]
            for cat_name, runner_fn in runners:
                warning: str | None = None
                try:
                    score, cat_turns = runner_fn()
                except Exception as error:
                    warning = f"category_error:{cat_name}:{type(error).__name__}"
                    warnings.append(warning)
                    cat_turns = []
                    score = compute_category_score(
                        [
                            CheckResult(
                                id=f"{cat_name}_error",
                                category=cat_name,
                                name=f"Category {cat_name} completed without unexpected error",
                                passed=False,
                                reason=f"unexpected_{type(error).__name__}",
                            )
                        ],
                        skipped=False,
                    )
                    print(f"[iris-bench] Warning in {cat_name}: {type(error).__name__}: {error}")
                cat_scores[cat_name] = score
                turns.extend(cat_turns)
                if on_category_done is not None:
                    on_category_done(cat_name, score, cat_turns, warning)
        finally:
            brain.lifetime.close()
    return cat_scores, turns, warnings


def run_benchmark(
    engine: str,
    model: str,
    *,
    config_path: Path | None = None,
    base_url: str | None = None,
    api_model: str | None = None,
    warmup_turns: int = 2,
    runs: int = 1,
    mock_tools: bool = False,
    synthetic_memory: bool = False,
    lamp_device: str | None = None,
    comments: str = "",
    model_context_tokens: int | None = None,
    model_file: str | None = None,
    benchmark_label: str | None = None,
    reasoning_mode: Literal["off", "low", "on", "server"] | None = None,
    reasoning_budget_tokens: int | None = None,
    max_tokens: int | None = None,
    cache_dir: Path = BENCH_CACHE_DIR,
    results_dir: Path = DEFAULT_RESULTS_DIR,
    client_transport: httpx.BaseTransport | None = None,
) -> RunOutcome:
    """Run the full Iris Bench suite unattended and write sanitized public + private files."""
    if not engine.strip() or not model.strip():
        raise ValueError("Both engine and model names must be non-blank.")
    if warmup_turns < 0 or warmup_turns > 10:
        raise ValueError("warmup_turns must be between 0 and 10.")
    if runs < 1 or runs > 10:
        raise ValueError("runs must be between 1 and 10.")

    resolved_cache = cache_dir.expanduser().resolve()
    resolved_cache.mkdir(parents=True, exist_ok=True)
    dotenv = load_dotenv_if_present(PROJECT_ROOT)
    base_settings, _ = _load_bench_settings(config_path, dotenv=dotenv)
    settings_updates: dict[str, object] = {}
    if reasoning_mode is not None:
        settings_updates["reasoning_mode"] = reasoning_mode
    target_mode = reasoning_mode if reasoning_mode is not None else base_settings.reasoning_mode
    if max_tokens is not None:
        if max_tokens < 1 or max_tokens > 32768:
            raise ValueError("max_tokens must be between 1 and 32768.")
        if target_mode in {"low", "on"}:
            settings_updates["reasoning_max_tokens"] = max_tokens
        else:
            settings_updates["max_tokens"] = max_tokens
    if model_context_tokens is not None:
        settings_updates["model_context_tokens"] = model_context_tokens
    if settings_updates:
        base_settings = base_settings.model_copy(update=settings_updates)
        base_settings.validate_constraints()
    local_cfg = load_local_bench_config(resolved_cache)

    live_db = database_path(base_settings) if base_settings.memory_enabled else None
    embedding_dir: Path | None = None
    if base_settings.memory_semantic_enabled:
        candidate_dir = model_directory(base_settings)
        try:
            verify_embeddings(candidate_dir)
            embedding_dir = candidate_dir
        except Exception:
            embedding_dir = None

    memory_mode: Literal["snapshot", "synthetic", "none"] = "none"
    snapshot_source: Path | None = None
    probes: list[MemoryProbe] = []
    synthetic_temp_path: Path | None = None

    if synthetic_memory:
        memory_mode = "synthetic"
        synthetic_temp_path = resolved_cache / ".tmp-synthetic-source.sqlite3"
        probe_file = create_synthetic_memory_db(
            synthetic_temp_path, owner=base_settings.memory_owner
        )
        probes = probe_file.probes
        snapshot_source = synthetic_temp_path
    elif local_cfg is not None and Path(local_cfg.snapshot_path).is_file():
        memory_mode = "snapshot"
        snapshot_source = Path(local_cfg.snapshot_path)
        probes_path = Path(local_cfg.probes_path)
        if probes_path.is_file():
            probe_file = MemoryProbeFile.model_validate_json(
                probes_path.read_text(encoding="utf-8")
            )
            probes = probe_file.probes

    effective_lamp = lamp_device or (local_cfg.lamp_device if local_cfg is not None else None)
    label_slug = (
        _slug(benchmark_label)
        if benchmark_label and benchmark_label.strip()
        else f"{_slug(engine)}_{_slug(model)}"
    )
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{label_slug}_{uuid4().hex[:6]}"
    timestamp_utc = datetime.now(UTC).isoformat(timespec="seconds")

    all_turns: list[TurnRecord] = []
    runs_categories: list[dict[str, CategoryScore]] = []
    current_pass_categories: dict[str, CategoryScore] = {}
    run_warnings: list[str] = []
    has_home_service = bool(base_settings.home_service_url) or mock_tools
    has_weather = base_settings.weather_enabled or mock_tools
    has_semantic = embedding_dir is not None and memory_mode != "none"

    resolved_results = results_dir.expanduser().resolve()
    resolved_results.mkdir(parents=True, exist_ok=True)
    public_path = resolved_results / f"{run_id}.json"
    transcript_dir = resolved_cache / "runs"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / f"{run_id}.json"

    iris_version, iris_commit = _iris_version_and_commit()
    hardware_info = detect_hardware()
    effective_ctx = (
        model_context_tokens
        if model_context_tokens is not None
        else base_settings.model_context_tokens
    )
    config_summary = ConfigSummary(
        temperature=base_settings.temperature,
        max_tokens=base_settings.effective_max_tokens(),
        model_context_tokens=effective_ctx,
        reasoning_mode=base_settings.reasoning_mode,
        reasoning_budget_tokens=reasoning_budget_tokens,
        tools_enabled=True,
        mock_tools=mock_tools,
        warmup_turns=warmup_turns,
        runs=runs,
        memory_mode=memory_mode,
        memory_probe_count=len(probes),
        has_semantic_memory=has_semantic,
        has_home_service=has_home_service,
        has_weather=has_weather,
        chat_template_kwargs=base_settings.effective_chat_template_kwargs(),
        chat_request_options=base_settings.effective_chat_request_options(),
    )
    clean_model_file = (
        PureWindowsPath(model_file).name if model_file and model_file.strip() else None
    )

    forbidden_terms: list[str] = []
    if memory_mode == "snapshot":
        if base_settings.weather_location:
            forbidden_terms.append(base_settings.weather_location)
        if effective_lamp and effective_lamp.strip().casefold() != "living room lamp":
            forbidden_terms.append(effective_lamp)
        for probe in probes:
            if not probe.judge_safe:
                forbidden_terms.append(probe.prompt)
                forbidden_terms.extend(probe.expected_any)
    for secret_val in (
        dotenv.get("OPENROUTER_API_KEY", ""),
        dotenv.get("IRIS_API_KEY", ""),
        dotenv.get("IRIS_HOME_SERVICE_TOKEN", ""),
        os.environ.get("OPENROUTER_API_KEY", ""),
        os.environ.get("IRIS_API_KEY", ""),
        os.environ.get("IRIS_HOME_SERVICE_TOKEN", ""),
    ):
        if secret_val:
            forbidden_terms.append(secret_val)

    def _flush_snapshot() -> RunOutcome:
        snapshot_runs = (
            [*runs_categories, current_pass_categories]
            if current_pass_categories
            else list(runs_categories)
        )
        categories = _merge_categories(snapshot_runs)
        speed = summarize_speed(all_turns)
        scores = build_scores(categories, speed, judge_quality=None)
        public_result = PublicBenchmarkResult(
            suite_version=SUITE_VERSION,
            run_id=run_id,
            timestamp_utc=timestamp_utc,
            iris_version=iris_version,
            iris_git_commit=iris_commit,
            engine=engine.strip(),
            model=model.strip(),
            model_file=clean_model_file,
            benchmark_label=benchmark_label.strip()
            if benchmark_label and benchmark_label.strip()
            else None,
            comments=comments.strip(),
            hardware=hardware_info,
            config=config_summary,
            scores=scores,
            speed=speed,
            categories=categories,
            judge=None,
            warnings=list(dict.fromkeys(run_warnings)),
        )
        transcript = PrivateRunTranscript(
            run_id=run_id,
            public_result_path=str(public_path),
            engine=engine.strip(),
            model=model.strip(),
            timestamp_utc=timestamp_utc,
            weather_location_to_scrub=base_settings.weather_location,
            lamp_device_to_scrub=effective_lamp or "",
            turns=list(all_turns),
        )
        _atomic_json_retry(transcript_path, transcript.model_dump(mode="json"))
        assert_public_privacy(public_result, forbidden_terms=forbidden_terms)
        _atomic_json_retry(public_path, public_result.model_dump(mode="json"))
        return RunOutcome(
            result=public_result,
            public_path=public_path,
            transcript=transcript,
            transcript_path=transcript_path,
        )

    inner_transport = client_transport or httpx.HTTPTransport(trust_env=False)
    transport: httpx.BaseTransport = (
        MockWeatherTransport(inner_transport, location_name=base_settings.weather_location)
        if mock_tools
        else inner_transport
    )

    def on_cat_done(
        cat_name: str,
        score: CategoryScore,
        cat_turns: list[TurnRecord],
        warning: str | None,
    ) -> None:
        current_pass_categories[cat_name] = score
        all_turns.extend(cat_turns)
        if warning:
            run_warnings.append(warning)
        _flush_snapshot()

    try:
        for run_idx in range(1, runs + 1):
            current_pass_categories.clear()

            with working_db_copy(snapshot_source, live_db_path=live_db) as working_db:
                bench_updates: dict[str, object] = {
                    "trace": False,
                    "notifications_enabled": False,
                    "tools_enabled": True,
                    "memory_enabled": memory_mode != "none",
                    "memory_database": working_db if memory_mode != "none" else None,
                    "memory_semantic_enabled": has_semantic,
                    "memory_embedding_model_dir": embedding_dir,
                    "memory_hierarchy_enabled": memory_mode != "none",
                    "memory_search_tool": memory_mode != "none",
                }
                if base_url is not None:
                    bench_updates["base_url"] = base_url
                if api_model is not None:
                    bench_updates["model"] = api_model
                if model_context_tokens is not None:
                    bench_updates["model_context_tokens"] = model_context_tokens
                if mock_tools:
                    bench_updates["weather_enabled"] = True
                    bench_updates["home_service_url"] = "http://127.0.0.1:8766"
                    bench_updates["home_service_token"] = SecretStr(
                        "mockhometoken12345678901234567890"
                    )

                run_settings = base_settings.model_copy(update=bench_updates)
                cat_scores, _, _ = _run_single_pass(
                    run_idx,
                    run_settings=run_settings,
                    transport=transport,
                    probes=probes,
                    effective_lamp=effective_lamp,
                    mock_tools=mock_tools,
                    warmup_turns=warmup_turns,
                    on_category_done=on_cat_done,
                )
                runs_categories.append(cat_scores)
                current_pass_categories.clear()
    except KeyboardInterrupt:
        run_warnings.append("interrupted_partial_run")
        print("\n[iris-bench] Interrupted — saving partial benchmark progress completed so far.")
    except Exception as error:
        if "Privacy check failed" in str(error):
            raise
        run_warnings.append(f"run_error:{type(error).__name__}")
        print(
            f"\n[iris-bench] Error during benchmark ({type(error).__name__}: {error}) — "
            "saving partial progress completed so far."
        )
    finally:
        if synthetic_temp_path is not None:
            synthetic_temp_path.unlink(missing_ok=True)
        if client_transport is None:
            transport.close()

    return _flush_snapshot()
