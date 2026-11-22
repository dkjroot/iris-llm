"""CLI entry point for Iris Bench: prepare, run, judge, report and full."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from iris_bench import (
    BENCH_CACHE_DIR,
    DEFAULT_JUDGE_MODEL,
    DEFAULT_REPORT_HTML,
    DEFAULT_RESULTS_DIR,
)
from iris_bench.card import ParsedModelCard, parse_model_card, prompt_for_model_card
from iris_bench.judge import (
    find_pending_run_pairs,
    judge_runs,
    load_run_pair,
)
from iris_bench.report import (
    format_markdown_table,
    format_terminal_table,
    load_results,
    render_html_report,
)
from iris_bench.runner import load_local_bench_config, prepare_benchmark, run_benchmark
from iris_paths import atomic_json


def _add_run_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "engine",
        nargs="?",
        default=None,
        help="Engine name and version label (omit to paste a model/engine card).",
    )
    parser.add_argument(
        "model",
        nargs="?",
        default=None,
        help="Model name and quant label (omit to paste a model/engine card).",
    )
    parser.add_argument(
        "--card",
        default=None,
        help="Path to a text file containing a model/engine card, or '-' for stdin.",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to local-config.json.")
    parser.add_argument("--base-url", default=None, help="Override OpenAI-compatible base URL.")
    parser.add_argument("--api-model", default=None, help="Override API model ID sent to server.")
    parser.add_argument(
        "--warmup", type=int, default=2, help="Unmeasured warm-up turns (default: 2)."
    )
    parser.add_argument("--runs", type=int, default=1, help="Metered suite passes (default: 1).")
    parser.add_argument(
        "--mock-tools",
        action="store_true",
        help="Use in-process mock home lamp and Open-Meteo weather instead of live services.",
    )
    parser.add_argument(
        "--synthetic-memory",
        action="store_true",
        help="Use a built-in synthetic SQLite memory DB instead of a snapshotted local DB.",
    )
    parser.add_argument(
        "--lamp-device",
        default=None,
        help=(
            "Home automation device name to inspect/toggle (default: auto-detect living room lamp)."
        ),
    )
    parser.add_argument(
        "--comments",
        default="",
        help="Optional subjective notes saved in the public JSON 'comments' field.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_RESULTS_DIR,
        help="Directory for GitHub-safe public result JSONs.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=BENCH_CACHE_DIR,
        help="Directory for private snapshots, probes and transcripts.",
    )


def _print_outcome_summary(outcome_result: object, public_path: Path) -> None:
    from iris_bench.models import PublicBenchmarkResult

    assert isinstance(outcome_result, PublicBenchmarkResult)
    print("\n--- Iris Bench Summary ---")
    print(f"Engine / Model : {outcome_result.engine} / {outcome_result.model}")
    print(f"Run ID         : {outcome_result.run_id}")
    print(
        f"Scores         : Overall={outcome_result.scores.overall_iris_score:.1f}  "
        f"Capability={outcome_result.scores.capability_score:.1f}%  "
        f"VoiceResp={outcome_result.scores.voice_responsiveness_score:.1f}"
        + (
            f"  Judge={outcome_result.scores.judge_quality_score:.1f}%"
            if outcome_result.scores.judge_quality_score is not None
            else ""
        )
    )
    tps = outcome_result.speed.median_tok_per_sec
    tps_text = (
        f"{tps:.1f} tok/s"
        if tps is not None
        else (
            f"~{outcome_result.speed.median_est_tok_per_sec:.1f} est tok/s"
            if outcome_result.speed.median_est_tok_per_sec is not None
            else "n/a"
        )
    )
    print(
        f"Speed (median) : TTFT={outcome_result.speed.median_ttft_ms} ms  "
        f"TTFS={outcome_result.speed.median_ttfs_ms} ms  "
        f"Model={outcome_result.speed.median_model_ms} ms  "
        f"Throughput={tps_text}"
    )
    for name, cat in outcome_result.categories.items():
        if cat.skipped:
            print(f"  - {name:<20} skipped")
        else:
            print(f"  - {name:<20} {cat.score:5.1f}% ({cat.passed}/{cat.total})")
    print(f"Public JSON    : {public_path}")


def _cmd_prepare(args: argparse.Namespace) -> int:
    summary = prepare_benchmark(
        config_path=args.config,
        db_path=args.db,
        lamp_device=args.lamp_device,
        judge_model=args.judge_model,
        cache_dir=args.cache_dir,
        force_probes=args.force_probes,
    )
    print(f"Snapshot saved : {summary.config.snapshot_path}")
    print(f"Table counts   : {summary.config.snapshot_counts}")
    status = "reused existing" if summary.probes_reused else "drafted new"
    print(
        f"Memory probes  : {summary.probe_count} probe(s) ({status}) -> "
        f"{summary.config.probes_path}"
    )
    print(
        "Review .cache/iris-bench/memory_probes.json if you want to tweak prompts or mark "
        "non-sensitive probes with judge_safe=true."
    )
    return 0


def _resolve_run_target(
    args: argparse.Namespace,
) -> tuple[ParsedModelCard, str | None, str | None]:
    if args.card is not None:
        raw_card = (
            sys.stdin.read()
            if args.card == "-"
            else Path(args.card).expanduser().read_text(encoding="utf-8")
        )
        card = parse_model_card(raw_card)
    elif args.engine and args.model:
        card = ParsedModelCard(engine=args.engine, model=args.model)
    elif getattr(args, "no_prompt", False):
        raise ValueError("Provide both 'engine' and 'model' (or '--card') when using --no-prompt.")
    else:
        card = prompt_for_model_card()

    base_url = args.base_url if args.base_url is not None else card.base_url
    api_model = args.api_model if args.api_model is not None else card.api_model
    print(f"\nTarget Engine  : {card.engine}")
    print(f"Target Model   : {card.model}")
    if base_url or api_model or card.model_context_tokens:
        details = [
            f"endpoint={base_url}" if base_url else "",
            f"alias={api_model}" if api_model else "",
            f"ctx={card.model_context_tokens}" if card.model_context_tokens else "",
        ]
        print("Server Details : " + ", ".join(part for part in details if part))
    if card.benchmark_label:
        print(f"Bench Label    : {card.benchmark_label}")
    print("Running benchmark suite unattended...")
    return card, base_url, api_model


def _cmd_run(args: argparse.Namespace) -> int:
    card, base_url, api_model = _resolve_run_target(args)
    outcome = run_benchmark(
        card.engine,
        card.model,
        config_path=args.config,
        base_url=base_url,
        api_model=api_model,
        warmup_turns=args.warmup,
        runs=args.runs,
        mock_tools=args.mock_tools,
        synthetic_memory=args.synthetic_memory,
        lamp_device=args.lamp_device,
        comments=args.comments,
        model_context_tokens=card.model_context_tokens,
        model_file=card.model_file,
        benchmark_label=card.benchmark_label,
        cache_dir=args.cache_dir,
        results_dir=args.output_dir,
    )
    _print_outcome_summary(outcome.result, outcome.public_path)
    print(f"Private trace  : {outcome.transcript_path}")
    return 0


def _refresh_report(results_dir: Path, html_path: Path) -> Path:
    results = load_results(results_dir)
    return render_html_report(results, html_path)


def _cmd_judge(args: argparse.Namespace) -> int:
    local_cfg = load_local_bench_config(args.cache_dir)
    judge_model = args.judge_model or (
        local_cfg.judge_model if local_cfg is not None else DEFAULT_JUDGE_MODEL
    )
    if args.pending:
        pairs = find_pending_run_pairs(cache_dir=args.cache_dir)
        if not pairs:
            print("No pending unjudged runs found.")
            return 0
    else:
        pairs = [load_run_pair(args.run_ref, cache_dir=args.cache_dir)]

    judge_errors: list[tuple[str, str]] = []
    outcomes = judge_runs(
        pairs,
        judge_model=judge_model,
        errors_out=judge_errors,
        cache_dir=args.cache_dir,
    )
    if not outcomes and not judge_errors:
        print("Judging cancelled; no data was sent to OpenRouter.")
        return 0
    for item in outcomes:
        j = item.public_result.judge
        print(
            f"Judged {item.run_id}: Judge={j.overall_score if j else 'n/a'}%  "
            f"Overall={item.public_result.scores.overall_iris_score:.1f}"
        )
    if outcomes and args.results_dir.expanduser().is_dir():
        report_path = _refresh_report(args.results_dir, args.html)
        print(f"Updated HTML   : {report_path}")
    if judge_errors:
        print("\nJudge errors:", file=sys.stderr)
        for run_id, msg in judge_errors:
            print(f"  - {run_id}: {msg}", file=sys.stderr)
        return 1
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    results = load_results(args.results_dir)
    if args.markdown:
        print(format_markdown_table(results))
    else:
        print(format_terminal_table(results))
    report_path = render_html_report(results, args.html)
    print(f"\nHTML report written to {report_path}")
    return 0


def _cmd_full(args: argparse.Namespace) -> int:
    card, base_url, api_model = _resolve_run_target(args)
    outcome = run_benchmark(
        card.engine,
        card.model,
        config_path=args.config,
        base_url=base_url,
        api_model=api_model,
        warmup_turns=args.warmup,
        runs=args.runs,
        mock_tools=args.mock_tools,
        synthetic_memory=args.synthetic_memory,
        lamp_device=args.lamp_device,
        comments=args.comments,
        model_context_tokens=card.model_context_tokens,
        model_file=card.model_file,
        benchmark_label=card.benchmark_label,
        cache_dir=args.cache_dir,
        results_dir=args.output_dir,
    )
    _print_outcome_summary(outcome.result, outcome.public_path)
    print(f"Private trace  : {outcome.transcript_path}")

    post_errors: list[str] = [f"run_warning: {w}" for w in outcome.result.warnings]
    report_path: Path | None = None
    try:
        report_path = _refresh_report(args.output_dir, args.html)
    except Exception as error:
        post_errors.append(f"report_error: {type(error).__name__}: {error}")

    if not args.no_prompt:
        outcome_result = outcome.result
        if not args.comments:
            try:
                note = input(
                    "\nOptional one-line subjective comment for the public JSON (Enter to skip): "
                ).strip()
                if note:
                    updated = outcome.result.model_copy(update={"comments": note})
                    atomic_json(outcome.public_path, updated.model_dump(mode="json"))
                    outcome_result = updated
            except (EOFError, OSError) as error:
                post_errors.append(f"comment_error: {type(error).__name__}: {error}")

        if not args.no_judge:
            local_cfg = load_local_bench_config(args.cache_dir)
            judge_model = args.judge_model or (
                local_cfg.judge_model if local_cfg is not None else DEFAULT_JUDGE_MODEL
            )
            judge_errors: list[tuple[str, str]] = []
            try:
                pair = load_run_pair(outcome_result.run_id, cache_dir=args.cache_dir)
                judge_runs(
                    [pair],
                    judge_model=judge_model,
                    errors_out=judge_errors,
                    cache_dir=args.cache_dir,
                )
            except Exception as error:
                post_errors.append(f"judge_error: {type(error).__name__}: {error}")
            for _, msg in judge_errors:
                post_errors.append(f"judge_error: {msg}")
        try:
            report_path = _refresh_report(args.output_dir, args.html)
        except Exception as error:
            post_errors.append(f"report_error: {type(error).__name__}: {error}")

    try:
        results = load_results(args.output_dir)
        print("\n" + format_terminal_table(results))
    except Exception as error:
        post_errors.append(f"leaderboard_error: {type(error).__name__}: {error}")
    if report_path is not None:
        print(f"\nHTML report    : {report_path}")
    if args.no_judge or args.no_prompt:
        print(
            "Tip: run `iris-bench judge --pending` later to batch-judge unattended runs "
            "in one prompt."
        )
    if post_errors:
        print("\nErrors / Warnings encountered (saved partial/completed outputs above):")
        for err in post_errors:
            print(f"  - {err}")
        if any(err.startswith("judge_error:") for err in post_errors):
            print(f"Tip: re-run judging later with: iris-bench judge {outcome.result.run_id}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="iris-bench",
        description="Benchmark local LLMs across real Iris capabilities and voice responsiveness.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prepare", help="Create a read-only DB snapshot and draft probes.")
    p_prep.add_argument("--config", type=Path, default=None, help="Path to local-config.json.")
    p_prep.add_argument("--db", type=Path, default=None, help="Override Iris SQLite DB path.")
    p_prep.add_argument(
        "--lamp-device", default=None, help="Remembered home lamp device name for this setup."
    )
    p_prep.add_argument(
        "--judge-model",
        default=DEFAULT_JUDGE_MODEL,
        help=f"OpenRouter judge model (default: {DEFAULT_JUDGE_MODEL}).",
    )
    p_prep.add_argument(
        "--force-probes",
        action="store_true",
        help="Overwrite an existing memory_probes.json instead of keeping local edits.",
    )
    p_prep.add_argument("--cache-dir", type=Path, default=BENCH_CACHE_DIR)
    p_prep.set_defaults(func=_cmd_prepare)

    p_run = sub.add_parser("run", help="Run the unattended benchmark suite on the active model.")
    _add_run_flags(p_run)
    p_run.set_defaults(func=_cmd_run)

    p_judge = sub.add_parser(
        "judge", help="Review sanitized transcripts and run OpenRouter LLM-as-a-judge."
    )
    p_judge.add_argument(
        "run_ref",
        nargs="?",
        default="latest",
        help="Run ID to judge, or 'latest' (default: latest).",
    )
    p_judge.add_argument(
        "--pending",
        action="store_true",
        help="Batch-review and judge all unjudged local runs under one confirmation prompt.",
    )
    p_judge.add_argument("--judge-model", default=None, help="Override OpenRouter judge model.")
    p_judge.add_argument("--cache-dir", type=Path, default=BENCH_CACHE_DIR)
    p_judge.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    p_judge.add_argument("--html", type=Path, default=DEFAULT_REPORT_HTML)
    p_judge.set_defaults(func=_cmd_judge)

    p_report = sub.add_parser("report", help="Render leaderboard table and graphical HTML report.")
    p_report.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    p_report.add_argument("--html", type=Path, default=DEFAULT_REPORT_HTML)
    p_report.add_argument("--markdown", action="store_true", help="Output Markdown table.")
    p_report.set_defaults(func=_cmd_report)

    p_full = sub.add_parser(
        "full",
        help="Run unattended benchmark, then optionally batch-prompt for comment + judge + report.",
    )
    _add_run_flags(p_full)
    p_full.add_argument("--judge-model", default=None, help="Override OpenRouter judge model.")
    p_full.add_argument(
        "--no-judge",
        action="store_true",
        help="Skip OpenRouter judging during this command (you can run judge --pending later).",
    )
    p_full.add_argument(
        "--no-prompt",
        action="store_true",
        help="Run 100% non-interactively (skips comment prompt and judge approval prompt).",
    )
    p_full.add_argument("--html", type=Path, default=DEFAULT_REPORT_HTML)
    p_full.set_defaults(func=_cmd_full)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (FileNotFoundError, ValueError, OSError, RuntimeError) as error:
        print(f"iris-bench error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
