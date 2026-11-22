"""Privacy-gated OpenRouter LLM-as-a-judge with single batched preview and approval."""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from iris_bench import BENCH_CACHE_DIR, DEFAULT_JUDGE_MODEL
from iris_bench.models import JudgeScoreSummary, PrivateRunTranscript, PublicBenchmarkResult
from iris_bench.scoring import recompute_scores
from iris_bench.snapshot import load_dotenv_if_present
from iris_paths import PROJECT_ROOT, atomic_json
from iris_settings import OPENROUTER_BASE_URL

JUDGE_DIMENSIONS = (
    "conversational_naturalness",
    "memory_incorporation",
    "expression_fit",
    "personality_adherence",
    "tool_honesty",
)

_SYSTEM_PROMPT = """You are an impartial evaluator for Iris, a local voice companion.
Score each sanitized conversation case below on a 0 to 5 scale (half-points allowed) ONLY for
the dimensions relevant to that case (omit or set null for irrelevant dimensions):
- conversational_naturalness: warm, concise, natural spoken cadence (not robotic or overly wordy)
- memory_incorporation: accurately and naturally uses provided/recalled memory when applicable
- expression_fit: chosen face expression matches the emotional tone of the prompt and reply
- personality_adherence: stays convincingly in the requested persona without breaking character
- tool_honesty: uses tools honestly and accurately reflects tool outcomes without inventing facts

Output ONLY raw JSON starting with '{' and ending with '}'. Do not include any preamble,
markdown headings, bullet lists, or code fences. Schema:
{
  "cases": [
    {
      "case_id": "<exact case_id>",
      "scores": {
        "conversational_naturalness": 4.5,
        "memory_incorporation": null,
        "expression_fit": 5.0,
        "personality_adherence": null,
        "tool_honesty": null
      },
      "note": "One short sentence."
    }
  ]
}
"""


class JudgeCaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    category: str
    prompt: str
    rubric: str
    reply: str
    expression: str | None = None
    tools_used: list[str] = Field(default_factory=list)


class JudgePacket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    engine: str
    model: str
    excluded_private_count: int
    cases: list[JudgeCaseInput] = Field(default_factory=list)


class JudgeCaseOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    case_id: str
    scores: dict[str, float | None] = Field(default_factory=dict)
    note: str = ""


class JudgeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    cases: list[JudgeCaseOutput] = Field(default_factory=list)


@dataclass(frozen=True)
class LoadedRunPair:
    transcript_path: Path
    transcript: PrivateRunTranscript
    public_path: Path
    public_result: PublicBenchmarkResult


@dataclass(frozen=True)
class JudgedRunOutcome:
    run_id: str
    public_path: Path
    public_result: PublicBenchmarkResult


def resolve_judge_api_key(root: Path = PROJECT_ROOT) -> str:
    """Resolve OpenRouter API key from environment or .env without logging or storing it."""
    dotenv = load_dotenv_if_present(root)
    for var in ("IRIS_BENCH_JUDGE_API_KEY", "OPENROUTER_API_KEY", "IRIS_API_KEY"):
        val = os.environ.get(var, "").strip() or dotenv.get(var, "").strip()
        if val:
            return val
    return ""


def _scrub_text(text: str, weather_location: str, lamp_device: str) -> str:
    cleaned = text
    loc = weather_location.strip()
    if len(loc) >= 3:
        cleaned = re.sub(re.escape(loc), "[configured location]", cleaned, flags=re.IGNORECASE)
    lamp = lamp_device.strip()
    if len(lamp) >= 3 and lamp.casefold() != "living room lamp":
        cleaned = re.sub(re.escape(lamp), "living room lamp", cleaned, flags=re.IGNORECASE)
    return cleaned


def build_judge_packet(transcript: PrivateRunTranscript) -> JudgePacket:
    """Build a sanitized OpenRouter payload excluding private unvetted memory probes."""
    cases: list[JudgeCaseInput] = []
    excluded = 0
    for turn in transcript.turns:
        if turn.category == "memory_extraction":
            continue
        if not turn.judge_safe:
            excluded += 1
            continue
        reply_text = turn.spoken_text.strip() or f"[error: {turn.error or 'empty'}]"
        cases.append(
            JudgeCaseInput(
                case_id=turn.case_id,
                category=turn.category,
                prompt=_scrub_text(
                    turn.prompt,
                    transcript.weather_location_to_scrub,
                    transcript.lamp_device_to_scrub,
                ),
                rubric=_scrub_text(
                    turn.judge_rubric,
                    transcript.weather_location_to_scrub,
                    transcript.lamp_device_to_scrub,
                ),
                reply=_scrub_text(
                    reply_text,
                    transcript.weather_location_to_scrub,
                    transcript.lamp_device_to_scrub,
                ),
                expression=turn.expression,
                tools_used=[call.name for call in turn.tool_calls],
            )
        )
    return JudgePacket(
        run_id=transcript.run_id,
        engine=transcript.engine,
        model=transcript.model,
        excluded_private_count=excluded,
        cases=cases,
    )


def format_packet_preview(packets: list[JudgePacket], judge_model: str) -> str:
    """Render a clear human-readable summary of everything about to be sent to OpenRouter."""
    total_cases = sum(len(p.cases) for p in packets)
    total_excluded = sum(p.excluded_private_count for p in packets)
    approx_bytes = len(
        json.dumps([p.model_dump(mode="json") for p in packets], ensure_ascii=False).encode("utf-8")
    )
    lines = [
        "=" * 72,
        "OPENROUTER LLM-AS-A-JUDGE PRIVACY PREVIEW",
        f"Endpoint : {OPENROUTER_BASE_URL}/chat/completions",
        f"Model    : {judge_model}",
        f"Runs     : {len(packets)}",
        f"Included : {total_cases} sanitized case(s) (~{approx_bytes / 1024:.1f} KB)",
        f"Excluded : {total_excluded} private case(s) (judge_safe=false)",
        "=" * 72,
    ]
    for packet in packets:
        lines.append(
            f"\nRun: {packet.engine} / {packet.model} ({packet.run_id}) "
            f"[{len(packet.cases)} included, {packet.excluded_private_count} private excluded]"
        )
        for idx, case in enumerate(packet.cases, start=1):
            tools = ", ".join(case.tools_used) if case.tools_used else "none"
            lines.append(f"  [{idx}] {case.category} / {case.case_id}")
            lines.append(f"      Prompt : {case.prompt}")
            lines.append(
                f"      Reply  : {case.reply} (expr: {case.expression or 'none'}, tools: {tools})"
            )
            if case.rubric:
                lines.append(f"      Rubric : {case.rubric}")
    lines.append("=" * 72)
    return "\n".join(lines)


def load_run_pair(run_ref: str, *, cache_dir: Path = BENCH_CACHE_DIR) -> LoadedRunPair:
    """Load a local transcript and its linked public result by run_id or 'latest'."""
    runs_dir = cache_dir.expanduser().resolve() / "runs"
    if not runs_dir.is_dir():
        raise FileNotFoundError("No benchmark runs found under .cache/iris-bench/runs/.")
    if run_ref == "latest":
        candidates = sorted(
            runs_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise FileNotFoundError("No benchmark transcripts found under .cache/iris-bench/runs/.")
        transcript_path = candidates[0]
    else:
        transcript_path = runs_dir / (run_ref if run_ref.endswith(".json") else f"{run_ref}.json")
        if not transcript_path.is_file():
            raise FileNotFoundError(f"Transcript not found: {transcript_path}")
    transcript = PrivateRunTranscript.model_validate_json(
        transcript_path.read_text(encoding="utf-8")
    )
    public_path = Path(transcript.public_result_path)
    if not public_path.is_file():
        raise FileNotFoundError(f"Linked public result JSON not found: {public_path}")
    public_result = PublicBenchmarkResult.model_validate_json(
        public_path.read_text(encoding="utf-8")
    )
    return LoadedRunPair(
        transcript_path=transcript_path,
        transcript=transcript,
        public_path=public_path,
        public_result=public_result,
    )


def find_pending_run_pairs(*, cache_dir: Path = BENCH_CACHE_DIR) -> list[LoadedRunPair]:
    """Return all local runs whose linked public JSON does not yet have judge scores."""
    runs_dir = cache_dir.expanduser().resolve() / "runs"
    if not runs_dir.is_dir():
        return []
    pairs: list[LoadedRunPair] = []
    for path in sorted(runs_dir.glob("*.json")):
        try:
            transcript = PrivateRunTranscript.model_validate_json(path.read_text(encoding="utf-8"))
            public_path = Path(transcript.public_result_path)
            if not public_path.is_file():
                continue
            public_result = PublicBenchmarkResult.model_validate_json(
                public_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError):
            continue
        if public_result.judge is None:
            pairs.append(
                LoadedRunPair(
                    transcript_path=path,
                    transcript=transcript,
                    public_path=public_path,
                    public_result=public_result,
                )
            )
    return pairs


def interactive_confirm_packets(
    packets: list[JudgePacket],
    judge_model: str,
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> bool:
    """Display the batched human-readable preview once and require explicit 'y' approval."""
    print_fn(format_packet_preview(packets, judge_model))
    while True:
        prompt_text = (
            "Send this packet to OpenRouter? "
            "[y] Send all / [v] View raw JSON / [n] Cancel (default: n): "
        )
        answer = input_fn(prompt_text).strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer == "v":
            raw = json.dumps(
                [p.model_dump(mode="json") for p in packets],
                indent=2,
                ensure_ascii=False,
            )
            print_fn(raw)
            continue
        return False


_FENCE_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL | re.IGNORECASE)


def _parse_judge_response(content: str) -> JudgeResponse:
    """Extract and validate a JudgeResponse JSON object from raw or prose-wrapped output."""
    stripped = content.strip()
    try:
        return JudgeResponse.model_validate_json(stripped)
    except ValidationError:
        pass

    for match in _FENCE_RE.finditer(content):
        candidate = match.group(1).strip()
        try:
            return JudgeResponse.model_validate_json(candidate)
        except ValidationError:
            continue

    decoder = json.JSONDecoder()
    candidates: list[JudgeResponse] = []
    idx = 0
    while idx < len(content):
        brace = content.find("{", idx)
        if brace == -1:
            break
        try:
            obj, end = decoder.raw_decode(content, brace)
        except ValueError:
            idx = brace + 1
            continue
        if isinstance(obj, dict) and "cases" in obj:
            try:
                candidates.append(JudgeResponse.model_validate(obj))
            except ValidationError:
                pass
        idx = max(brace + 1, end)
    if candidates:
        return candidates[0]

    raise ValueError("Judge response did not contain a valid JudgeResponse JSON object.")


def _post_judge_chat(
    client: httpx.Client,
    *,
    judge_model: str,
    api_key: str,
    messages: list[dict[str, str]],
    use_json_format: bool,
) -> tuple[str, bool]:
    payload: dict[str, object] = {
        "model": judge_model,
        "temperature": 0.1,
        "max_tokens": 8192,
        "messages": messages,
    }
    if use_json_format:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    response = client.post(
        f"{OPENROUTER_BASE_URL}/chat/completions",
        json=payload,
        headers=headers,
    )
    if use_json_format and response.status_code in {400, 422}:
        # Some routed providers reject response_format; retry once without it.
        payload.pop("response_format", None)
        response = client.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            json=payload,
            headers=headers,
        )
        use_json_format = False
    response.raise_for_status()
    body = response.json()
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Judge response contained no choices")
    message = choices[0].get("message", {})
    content = message.get("content", "") if isinstance(message, dict) else ""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Judge response content was empty")
    return content, use_json_format


def _save_last_judge_error(content: str, cache_dir: Path = BENCH_CACHE_DIR) -> None:
    try:
        target = cache_dir.expanduser().resolve() / "last_judge_error.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content[:8000], encoding="utf-8", newline="\n")
    except OSError:
        pass


def _call_openrouter_judge(
    packet: JudgePacket,
    *,
    judge_model: str,
    api_key: str,
    transport: httpx.BaseTransport | None = None,
    cache_dir: Path = BENCH_CACHE_DIR,
) -> JudgeResponse:
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {"cases": [c.model_dump(mode="json") for c in packet.cases]},
                ensure_ascii=False,
            ),
        },
    ]
    with httpx.Client(transport=transport, trust_env=False, timeout=90.0) as client:
        content, used_json_format = _post_judge_chat(
            client,
            judge_model=judge_model,
            api_key=api_key,
            messages=messages,
            use_json_format=True,
        )
        try:
            return _parse_judge_response(content)
        except ValueError:
            # One automatic JSON-repair pass if the judge returned prose/markdown.
            repair_messages = [
                *messages,
                {"role": "assistant", "content": content[:4000]},
                {
                    "role": "user",
                    "content": (
                        "Your previous reply was not valid JSON. Output ONLY a raw JSON object "
                        'matching {"cases": [{"case_id": "...", "scores": {...}, "note": "..."}]} '
                        "with no markdown or prose."
                    ),
                },
            ]
            repaired, _ = _post_judge_chat(
                client,
                judge_model=judge_model,
                api_key=api_key,
                messages=repair_messages,
                use_json_format=used_json_format,
            )
            try:
                return _parse_judge_response(repaired)
            except ValueError as err:
                _save_last_judge_error(repaired, cache_dir=cache_dir)
                raise ValueError(
                    "Judge did not return valid JSON (raw output saved to "
                    ".cache/iris-bench/last_judge_error.txt)."
                ) from err


def _summarize_judge_response(
    packet: JudgePacket,
    response: JudgeResponse,
    judge_model: str,
) -> tuple[JudgeScoreSummary, dict[str, str]]:
    dim_buckets: dict[str, list[float]] = defaultdict(list)
    case_scores: dict[str, float] = {}
    notes: dict[str, str] = {}
    valid_ids = {c.case_id for c in packet.cases}

    for item in response.cases:
        if item.case_id not in valid_ids:
            continue
        per_case_vals: list[float] = []
        for dim, val in item.scores.items():
            if val is None or dim not in JUDGE_DIMENSIONS:
                continue
            clamped = max(0.0, min(5.0, float(val))) * 20.0
            dim_buckets[dim].append(clamped)
            per_case_vals.append(clamped)
        if per_case_vals:
            case_scores[item.case_id] = round(sum(per_case_vals) / len(per_case_vals), 1)
        if item.note.strip():
            notes[item.case_id] = item.note.strip()

    dimensions = {dim: round(sum(vals) / len(vals), 1) for dim, vals in dim_buckets.items() if vals}
    all_vals = list(case_scores.values())
    overall = round(sum(all_vals) / len(all_vals), 1) if all_vals else 0.0
    summary = JudgeScoreSummary(
        judge_model=judge_model,
        evaluated_at_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        sample_count=len(packet.cases),
        excluded_private_count=packet.excluded_private_count,
        overall_score=overall,
        dimensions=dimensions,
        case_scores=case_scores,
    )
    return summary, notes


def judge_runs(
    pairs: list[LoadedRunPair],
    *,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    api_key: str | None = None,
    auto_approve: bool = False,
    confirm_fn: Callable[[list[JudgePacket], str], bool] | None = None,
    transport: httpx.BaseTransport | None = None,
    errors_out: list[tuple[str, str]] | None = None,
    cache_dir: Path = BENCH_CACHE_DIR,
) -> list[JudgedRunOutcome]:
    """Review and judge one or more runs under a single batched approval prompt."""
    if not pairs:
        return []
    packets = [build_judge_packet(pair.transcript) for pair in pairs]
    if not any(packet.cases for packet in packets):
        raise ValueError("No judge-safe cases are available to evaluate.")

    if not auto_approve:
        approver = confirm_fn or (lambda pkts, model: interactive_confirm_packets(pkts, model))
        if not approver(packets, judge_model):
            return []

    key = (api_key or resolve_judge_api_key()).strip()
    if not key:
        raise ValueError(
            "No OpenRouter API key found. Set OPENROUTER_API_KEY, IRIS_API_KEY or "
            "IRIS_BENCH_JUDGE_API_KEY in your environment or .env file."
        )

    outcomes: list[JudgedRunOutcome] = []
    for pair, packet in zip(pairs, packets, strict=True):
        if not packet.cases:
            continue
        try:
            response = _call_openrouter_judge(
                packet,
                judge_model=judge_model,
                api_key=key,
                transport=transport,
                cache_dir=cache_dir,
            )
            summary, notes = _summarize_judge_response(packet, response, judge_model)
            updated_public = recompute_scores(
                pair.public_result.model_copy(update={"judge": summary})
            )
            atomic_json(pair.public_path, updated_public.model_dump(mode="json"))

            updated_transcript = pair.transcript.model_copy(update={"judge_notes": notes})
            atomic_json(pair.transcript_path, updated_transcript.model_dump(mode="json"))
            outcomes.append(
                JudgedRunOutcome(
                    run_id=pair.transcript.run_id,
                    public_path=pair.public_path,
                    public_result=updated_public,
                )
            )
        except Exception as error:
            if errors_out is None:
                raise
            errors_out.append((pair.transcript.run_id, f"{type(error).__name__}: {error}"))
    return outcomes
