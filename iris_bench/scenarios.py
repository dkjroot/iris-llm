"""Benchmark scenario definitions, deterministic checks and independent home-state verification."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

from iris_app.replies import STREAM_PIPELINE, STREAM_REPLY
from iris_bench.models import (
    CategoryScore,
    CheckResult,
    MemoryProbe,
    ToolCallRecord,
    TurnRecord,
)
from iris_bench.scoring import compute_category_score
from iris_bench.telemetry import TurnTelemetryCollector
from iris_chat_engines.messages import ChatError
from iris_domain.expression import FACE_EXPRESSIONS
from iris_domain.personality import PersonalityContext
from iris_droid.protocol import write_wav
from iris_home.protocol import DeviceRequest, PowerState, Request, SwitchRequest
from iris_memory import Exchange
from iris_turn import TurnExpired, turn_budget

if TYPE_CHECKING:
    from iris_app.runtime import Brain
    from iris_bench.telemetry import BenchMockHomeClient, BenchSTT
    from iris_tools.home import HomeClient

_SILENT_WAV = write_wav(b"\x01\x00" * 1600, 16000)

_RAW_LEADING_CUE_RE = re.compile(
    r"^\s*(?:`{1,3}|\"|'|[*_]{1,3})?\[{1,2}\s*(?:expression|face)\s*[:=]\s*([a-zA-Z_-]+)\s*\]{1,2}",
    re.IGNORECASE,
)
_LEAKED_CUE_RE = re.compile(r"\[\[\s*(?:expression|face)\s*[:=][^\]]+\]\]", re.IGNORECASE)
_HTML_ENTITY_RE = re.compile(r"&(?:amp|apos|quot|lt|gt|#x27|#39);")
_RAW_TOOL_MARKER_RE = re.compile(
    r"\[\[(?:list_home_devices|get_home_state|set_home_power|read_home_meter|"
    r"get_home_schedule|save_home_schedule|get_local_time|get_local_weather|"
    r"send_notification|search_memory)\]\]"
)

BENCH_PERSONALITY = PersonalityContext(
    names=["Iris", "Victorian Butler"],
    active="Victorian Butler",
    prompt=(
        "Speak like a warm, dryly witty Victorian butler — polite, formal, using occasional "
        "phrasing such as 'very good', 'indeed', or 'if I may', while keeping replies brief "
        "and helpful."
    ),
)

_BUTLER_MARKERS = (
    "very good",
    "indeed",
    "if i may",
    "allow me",
    "certainly",
    "splendid",
    "quite",
    "good evening",
    "one must",
    "delighted",
    "shall i",
    "at your service",
    "sir",
    "madam",
)


@dataclass
class BenchRuntimeHandles:
    brain: Brain
    stt: BenchSTT
    collector: TurnTelemetryCollector
    home_client: HomeClient | BenchMockHomeClient | None
    lamp_device: str | None
    weather_enabled: bool
    mock_tools: bool
    weather_location: str


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _has_explicit_leading_cue(raw_reply: str) -> bool:
    match = _RAW_LEADING_CUE_RE.match(raw_reply)
    if match is None:
        return False
    return match.group(1).strip().lower() in FACE_EXPRESSIONS


def _tool_names(record: TurnRecord) -> list[str]:
    return [call.name for call in record.tool_calls]


def _last_tool_payload(record: TurnRecord, name: str) -> dict[str, object] | None:
    for call in reversed(record.tool_calls):
        if call.name == name:
            try:
                data = json.loads(call.raw_result)
                if isinstance(data, dict):
                    return data
            except ValueError:
                return None
    return None


def _run_voice_turn(
    handles: BenchRuntimeHandles,
    *,
    session: str,
    case_id: str,
    category: str,
    turn_index: int,
    prompt: str,
    face: bool = True,
    personality: PersonalityContext | None = None,
    judge_safe: bool = True,
    judge_rubric: str = "",
) -> TurnRecord:
    """Execute one turn through Brain.turn using Iris's streaming sentence pipeline."""
    handles.stt.text = prompt
    trace = handles.collector.start_turn()
    started = trace.started
    spoken_text = ""
    expression: str | None = None
    error_text: str | None = None
    memory_sources: list[str] = []

    def sink(reply: object, chunks: object, rate: int) -> None:
        del reply, rate
        for _ in chunks:  # type: ignore[attr-defined]
            pass

    pipe_token = STREAM_PIPELINE.set(True)
    reply_token = STREAM_REPLY.set(sink)
    try:
        reply = handles.brain.turn(
            session,
            _SILENT_WAV,
            text_only=False,
            face=face,
            personality=personality,
            audio_sink=sink,
        )
        spoken_text = reply.text
        expression = reply.expression
        chat = handles.brain.sessions.get(session)
        if chat is not None:
            memory_sources = sorted(chat.memory_sources)
    except (ChatError, TurnExpired, ValueError, RuntimeError) as error:
        error_text = f"{type(error).__name__}: {error}"
    finally:
        STREAM_REPLY.reset(reply_token)
        STREAM_PIPELINE.reset(pipe_token)
        finished = handles.collector.finish_turn() or trace

    turn_ms = round((perf_counter() - started) * 1000.0, 2)
    ttfs_ms = (
        round((finished.first_sentence_at - started) * 1000.0, 2)
        if finished.first_sentence_at is not None
        else None
    )
    tool_ms = round(sum(call.duration_ms for call in finished.tool_calls), 2)
    model_ms = (
        round(sum(call.total_model_ms for call in finished.llm_calls), 2)
        if finished.llm_calls
        else None
    )
    final_call = next(
        (call for call in reversed(finished.llm_calls) if call.raw_content),
        finished.llm_calls[-1] if finished.llm_calls else None,
    )
    first_call = finished.llm_calls[0] if finished.llm_calls else None

    return TurnRecord(
        case_id=case_id,
        category=category,
        turn_index=turn_index,
        prompt=prompt,
        raw_reply=final_call.raw_content if final_call is not None else "",
        spoken_text=spoken_text,
        expression=expression,
        streamed_sentences=list(finished.sentences),
        ttft_ms=first_call.ttft_ms if first_call is not None else None,
        ttfs_ms=ttfs_ms,
        model_ms=model_ms,
        tool_ms=tool_ms,
        turn_ms=turn_ms,
        prompt_tokens=final_call.prompt_tokens if final_call is not None else None,
        completion_tokens=(
            sum(c.completion_tokens for c in finished.llm_calls if c.completion_tokens) or None
        ),
        tokens_per_sec=final_call.tokens_per_sec if final_call is not None else None,
        est_tokens_per_sec=final_call.est_tokens_per_sec if final_call is not None else None,
        prompt_tokens_per_sec=final_call.prompt_tokens_per_sec if final_call is not None else None,
        tool_calls=list(finished.tool_calls),
        memory_sources=memory_sources,
        judge_safe=judge_safe,
        judge_rubric=judge_rubric,
        error=error_text,
    )


def run_warmups(handles: BenchRuntimeHandles, warmup_turns: int, run_index: int) -> None:
    """Execute unmeasured warm-up turns so local model caches and KV slots settle."""
    if warmup_turns <= 0:
        return
    prompts = [
        "Hi Iris, give me a one-sentence greeting.",
        "Quick warm-up: what is the current local time?",
    ]
    session = f"bench-warmup-{run_index}"
    for idx in range(warmup_turns):
        _run_voice_turn(
            handles,
            session=session,
            case_id=f"warmup_{idx + 1}",
            category="warmup",
            turn_index=idx + 1,
            prompt=prompts[idx % len(prompts)],
            face=True,
            judge_safe=False,
        )
    handles.brain.session_store.pop(session)


def run_conversation_category(
    handles: BenchRuntimeHandles, run_index: int
) -> tuple[CategoryScore, list[TurnRecord]]:
    session = f"bench-conv-{run_index}"
    turns: list[TurnRecord] = []
    checks: list[CheckResult] = []
    prompts = [
        (
            "conv_01",
            "Hi Iris, in a couple of sentences, why is the sky blue?",
            90,
            "Answer clearly in roughly two spoken sentences with no tools.",
        ),
        (
            "conv_02",
            "Can you explain that again as if I'm ten years old, in two short sentences?",
            65,
            "Simplify the explanation for a ten-year-old in two short sentences.",
        ),
        (
            "conv_03",
            "Great, thanks, that makes sense.",
            45,
            (
                "Acknowledge politely and briefly without tacking on an unnecessary "
                "follow-up question."
            ),
        ),
    ]
    for idx, (cid, prompt, max_words, rubric) in enumerate(prompts, start=1):
        rec = _run_voice_turn(
            handles,
            session=session,
            case_id=cid,
            category="conversation",
            turn_index=idx,
            prompt=prompt,
            face=True,
            judge_safe=True,
            judge_rubric=rubric,
        )
        turns.append(rec)
        ok_reply = rec.error is None and bool(rec.spoken_text.strip())
        checks.append(
            CheckResult(
                id=f"{cid}_reply",
                category="conversation",
                name=f"Turn {idx} produced clean spoken reply",
                passed=ok_reply
                and not _LEAKED_CUE_RE.search(rec.spoken_text)
                and not _HTML_ENTITY_RE.search(rec.spoken_text)
                and not _RAW_TOOL_MARKER_RE.search(rec.spoken_text),
                reason="ok" if ok_reply else (rec.error or "empty_or_leaked_reply"),
            )
        )
        checks.append(
            CheckResult(
                id=f"{cid}_brevity",
                category="conversation",
                name=f"Turn {idx} stayed within spoken brevity budget (<= {max_words} words)",
                passed=ok_reply and _word_count(rec.spoken_text) <= max_words,
                reason=(
                    "ok" if ok_reply and _word_count(rec.spoken_text) <= max_words else "too_long"
                ),
            )
        )
        checks.append(
            CheckResult(
                id=f"{cid}_no_tools",
                category="conversation",
                name=f"Turn {idx} answered ordinary chat without tool calls",
                passed=rec.error is None and not rec.tool_calls,
                reason="ok" if not rec.tool_calls else "unexpected_tool_call",
            )
        )
        if idx == 3:
            no_q = ok_reply and "?" not in rec.spoken_text
            checks.append(
                CheckResult(
                    id=f"{cid}_no_unneeded_question",
                    category="conversation",
                    name="Closing turn avoided unnecessary follow-up question",
                    passed=no_q,
                    weight=0.8,
                    reason="ok" if no_q else "asked_unneeded_followup",
                )
            )
    handles.brain.session_store.pop(session)
    return compute_category_score(checks), turns


def run_long_conversation_category(
    handles: BenchRuntimeHandles, run_index: int
) -> tuple[CategoryScore, list[TurnRecord]]:
    session = f"bench-long-{run_index}"
    turns: list[TurnRecord] = []
    checks: list[CheckResult] = []
    prompts = [
        (
            "long_01",
            "I'm planning a small weekend project: a bookshelf made of oak wood, 80 centimetres "
            "wide. Give me one quick tip on preparing the timber.",
            "Helpful concise timber preparation tip.",
        ),
        (
            "long_02",
            "What kind of simple wall brackets would suit a shelf that size?",
            "Relevant bracket advice for an 80 cm wooden shelf.",
        ),
        (
            "long_03",
            "Actually, let's change the wood from oak to pine, while keeping the width at "
            "80 centimetres. Does that change the bracket choice?",
            "Adapts advice to pine while keeping 80 cm width.",
        ),
        (
            "long_04",
            "Quick side question before I forget — what's the current time?",
            "Calls get_local_time cleanly mid-conversation.",
        ),
        (
            "long_05",
            "Back to the shelf — remind me what wood and width we settled on?",
            "Recalls the updated constraints: pine wood and 80 cm width (not oak).",
        ),
        (
            "long_06",
            "Lovely, that's all I need on the shelf for now. Thanks!",
            "Warm, brief closing turn.",
        ),
    ]
    for idx, (cid, prompt, rubric) in enumerate(prompts, start=1):
        rec = _run_voice_turn(
            handles,
            session=session,
            case_id=cid,
            category="long_conversation",
            turn_index=idx,
            prompt=prompt,
            face=True,
            judge_safe=True,
            judge_rubric=rubric,
        )
        turns.append(rec)
        ok = rec.error is None and bool(rec.spoken_text.strip())
        checks.append(
            CheckResult(
                id=f"{cid}_ok",
                category="long_conversation",
                name=f"Long turn {idx} completed cleanly",
                passed=ok,
                weight=0.7,
                reason="ok" if ok else (rec.error or "empty_reply"),
            )
        )
        if idx == 4:
            used_clock = "get_local_time" in _tool_names(rec)
            checks.append(
                CheckResult(
                    id="long_04_clock_tool",
                    category="long_conversation",
                    name="Mid-conversation live time request used get_local_time",
                    passed=ok and used_clock,
                    weight=1.2,
                    reason="ok" if used_clock else "missing_get_local_time",
                )
            )
        if idx == 5:
            low = rec.spoken_text.casefold()
            has_pine = "pine" in low
            has_width = "80" in low
            # Allow mentioning oak only if explicitly noting the switch away from it.
            bad_oak = "oak" in low and not any(
                phrase in low
                for phrase in ("instead of oak", "from oak", "rather than oak", "not oak")
            )
            retained = ok and has_pine and has_width and not bad_oak
            checks.append(
                CheckResult(
                    id="long_05_updated_constraints",
                    category="long_conversation",
                    name="Retained updated wood (pine) and width (80 cm) across 5 turns",
                    passed=retained,
                    weight=2.0,
                    reason="ok" if retained else "missed_or_stale_constraint",
                )
            )
    handles.brain.session_store.pop(session)
    return compute_category_score(checks), turns


def run_expressions_category(
    handles: BenchRuntimeHandles, run_index: int
) -> tuple[CategoryScore, list[TurnRecord]]:
    cases = [
        (
            "expr_01",
            "I just passed a really tough exam I've been revising for all month!",
            {"delighted", "proud", "amused", "playful"},
            "Congratulatory reply with an upbeat expression cue.",
        ),
        (
            "expr_02",
            "I dropped my favourite mug this morning and it smashed on the tiles.",
            {"concerned", "sad"},
            "Sympathetic reply with a concerned or sad expression cue.",
        ),
        (
            "expr_03",
            "Wait — why do mirrors flip left and right, but not top and bottom?",
            {"curious", "focused", "skeptical"},
            "Thoughtful reply with a curious/focused/skeptical expression cue.",
        ),
        (
            "expr_04",
            "Why don't scientists trust atoms? Because they make up everything!",
            {"amused", "playful", "delighted"},
            "Playful reaction to a joke with an amused/playful/delighted cue.",
        ),
    ]
    turns: list[TurnRecord] = []
    checks: list[CheckResult] = []
    seen_expressions: set[str] = set()

    for idx, (cid, prompt, expected_set, rubric) in enumerate(cases, start=1):
        session = f"bench-expr-{run_index}-{idx}"
        rec = _run_voice_turn(
            handles,
            session=session,
            case_id=cid,
            category="expressions",
            turn_index=idx,
            prompt=prompt,
            face=True,
            judge_safe=True,
            judge_rubric=rubric,
        )
        handles.brain.session_store.pop(session)
        turns.append(rec)

        explicit_cue = rec.error is None and _has_explicit_leading_cue(rec.raw_reply)
        checks.append(
            CheckResult(
                id=f"{cid}_cue_format",
                category="expressions",
                name=f"Case {idx} emitted explicit leading [[expression:...]] cue",
                passed=explicit_cue,
                reason="ok" if explicit_cue else "missing_explicit_leading_cue",
            )
        )
        tone_ok = explicit_cue and (rec.expression in expected_set)
        checks.append(
            CheckResult(
                id=f"{cid}_tone_match",
                category="expressions",
                name=f"Case {idx} chose an appropriate expression for the prompt",
                passed=tone_ok,
                reason="ok" if tone_ok else f"unexpected_expression:{rec.expression}",
            )
        )
        clean_speech = (
            rec.error is None
            and bool(rec.spoken_text.strip())
            and not _LEAKED_CUE_RE.search(rec.spoken_text)
            and all(not _LEAKED_CUE_RE.search(s) for s in rec.streamed_sentences)
        )
        checks.append(
            CheckResult(
                id=f"{cid}_no_cue_leak",
                category="expressions",
                name=f"Case {idx} kept expression cues out of spoken text",
                passed=clean_speech,
                reason="ok" if clean_speech else "leaked_cue_or_empty",
            )
        )
        if explicit_cue and rec.expression is not None:
            seen_expressions.add(rec.expression)

    varied = len(seen_expressions) >= 3
    checks.append(
        CheckResult(
            id="expr_variety",
            category="expressions",
            name="Used at least 3 distinct facial expressions across 4 emotional prompts",
            passed=varied,
            weight=1.2,
            reason="ok" if varied else f"only_{len(seen_expressions)}_distinct",
        )
    )
    return compute_category_score(checks), turns


def run_weather_time_category(
    handles: BenchRuntimeHandles, run_index: int
) -> tuple[CategoryScore, list[TurnRecord]]:
    session = f"bench-wt-{run_index}"
    turns: list[TurnRecord] = []
    checks: list[CheckResult] = []

    # Turn 1: Local time
    t1 = _run_voice_turn(
        handles,
        session=session,
        case_id="wt_01_time",
        category="tools_weather_time",
        turn_index=1,
        prompt="What is the current local date, weekday, and time right now?",
        face=True,
        judge_safe=True,
        judge_rubric="Calls get_local_time and reports accurate date/weekday/time naturally.",
    )
    turns.append(t1)
    clock_payload = _last_tool_payload(t1, "get_local_time")
    clock_called = t1.error is None and clock_payload is not None and "error" not in clock_payload
    checks.append(
        CheckResult(
            id="wt_01_tool_called",
            category="tools_weather_time",
            name="Local time query called get_local_time",
            passed=clock_called,
            reason="ok" if clock_called else "missing_or_failed_clock_tool",
        )
    )
    time_accurate = False
    if clock_called and clock_payload is not None:
        low = t1.spoken_text.casefold()
        weekday = str(clock_payload.get("weekday", "")).casefold()
        time_str = str(clock_payload.get("time", ""))
        hhmm = time_str[:5]
        hour_part = time_str[:2].lstrip("0") or "0"
        hour_12 = str((int(time_str[:2]) % 12) or 12) if len(time_str) >= 2 else ""
        time_accurate = bool(weekday and weekday in low) or (
            bool(hhmm and hhmm in low) or bool(hour_part in low) or bool(hour_12 and hour_12 in low)
        )
    checks.append(
        CheckResult(
            id="wt_01_reply_grounded",
            category="tools_weather_time",
            name="Time reply reflected tool clock output without raw tool markers",
            passed=(
                clock_called and time_accurate and not _RAW_TOOL_MARKER_RE.search(t1.spoken_text)
            ),
            reason="ok" if time_accurate else "time_not_grounded",
        )
    )

    # Turn 2: Weather (if enabled or mocked)
    if handles.weather_enabled or handles.mock_tools:
        t2 = _run_voice_turn(
            handles,
            session=session,
            case_id="wt_02_weather",
            category="tools_weather_time",
            turn_index=2,
            prompt="What is the weather like right now, and does it look like rain tomorrow?",
            face=True,
            judge_safe=True,
            judge_rubric=(
                "Calls get_local_weather, summarises current weather + tomorrow's rain chance, "
                "and credits Open-Meteo briefly."
            ),
        )
        turns.append(t2)
        w_payload = _last_tool_payload(t2, "get_local_weather")
        w_called = t2.error is None and w_payload is not None and "error" not in w_payload
        checks.append(
            CheckResult(
                id="wt_02_tool_called",
                category="tools_weather_time",
                name="Weather query called get_local_weather",
                passed=w_called,
                reason="ok" if w_called else "missing_or_failed_weather_tool",
            )
        )
        grounded = False
        credited = False
        if w_called and w_payload is not None:
            low = t2.spoken_text.casefold()
            credited = "open-meteo" in low or "open meteo" in low
            current = w_payload.get("current")
            candidates: list[str] = []
            if isinstance(current, dict):
                temp = current.get("temperature_2m")
                if isinstance(temp, (int, float)):
                    candidates.append(str(round(float(temp))))
                    candidates.append(f"{float(temp):.1f}")
                cond = current.get("conditions")
                if isinstance(cond, str) and cond:
                    candidates.extend(w.casefold() for w in cond.split() if len(w) >= 3)
            if candidates:
                grounded = any(token in low for token in candidates)
            else:
                grounded = bool(low.strip())
        checks.append(
            CheckResult(
                id="wt_02_reply_grounded",
                category="tools_weather_time",
                name="Weather reply reflected live tool figures/conditions",
                passed=w_called and grounded,
                reason="ok" if grounded else "weather_not_grounded",
            )
        )
        checks.append(
            CheckResult(
                id="wt_02_open_meteo_credit",
                category="tools_weather_time",
                name="Weather reply briefly credited Open-Meteo",
                passed=w_called and credited,
                weight=0.7,
                reason="ok" if credited else "missing_open_meteo_credit",
            )
        )

    handles.brain.session_store.pop(session)
    return compute_category_score(checks), turns


def _resolve_lamp_device(handles: BenchRuntimeHandles) -> str | None:
    if handles.home_client is None:
        return None
    if handles.lamp_device:
        return handles.lamp_device
    listing = handles.home_client.execute("list", Request())
    devices = listing.get("devices")
    if not isinstance(devices, list):
        return None
    names = [
        str(item["device"])
        for item in devices
        if isinstance(item, dict) and isinstance(item.get("device"), str)
    ]
    for name in names:
        if name.strip().casefold() == "living room lamp":
            return name
    for name in names:
        if "lamp" in name.casefold():
            return name
    return names[0] if len(names) == 1 else None


def _read_independent_state(
    home_client: HomeClient | BenchMockHomeClient, device: str
) -> str | None:
    payload = home_client.execute("status", DeviceRequest(device=device))
    state = payload.get("state")
    return str(state).lower() if isinstance(state, str) else None


def _switch_call_matches(calls: list[ToolCallRecord], target_state: str) -> bool:
    for call in calls:
        if call.name != "set_home_power" or not call.result_summary_ok:
            continue
        try:
            args = json.loads(call.arguments)
            if isinstance(args, dict) and str(args.get("state", "")).lower() == target_state:
                return True
        except ValueError:
            continue
    return False


def run_home_category(
    handles: BenchRuntimeHandles, run_index: int
) -> tuple[CategoryScore, list[TurnRecord]]:
    if handles.home_client is None:
        return compute_category_score([], skipped=True), []
    lamp = _resolve_lamp_device(handles)
    if lamp is None:
        return compute_category_score([], skipped=True), []
    initial_state = _read_independent_state(handles.home_client, lamp)
    if initial_state not in {"on", "off"}:
        return compute_category_score([], skipped=True), []

    session = f"bench-home-{run_index}"
    turns: list[TurnRecord] = []
    checks: list[CheckResult] = []
    first_target = "on" if initial_state == "off" else "off"
    second_target = initial_state

    try:
        # Turn 1: Inspect current state
        t1 = _run_voice_turn(
            handles,
            session=session,
            case_id="home_01_inspect",
            category="tools_home",
            turn_index=1,
            prompt=f"Please check whether the {lamp} is currently on or off.",
            face=True,
            judge_safe=True,
            judge_rubric=(
                "Calls get_home_state and truthfully states whether the lamp is on or off."
            ),
        )
        t1.independent_device_state = initial_state
        turns.append(t1)
        checked_state = t1.error is None and "get_home_state" in _tool_names(t1)
        t1_low = t1.spoken_text.casefold()
        state_word = re.search(rf"\b{re.escape(initial_state)}\b", t1_low) is not None
        checks.append(
            CheckResult(
                id="home_01_tool",
                category="tools_home",
                name="Status check called get_home_state",
                passed=checked_state,
                reason="ok" if checked_state else "missing_get_home_state",
            )
        )
        checks.append(
            CheckResult(
                id="home_01_truthful",
                category="tools_home",
                name="Status reply matched real device state",
                passed=checked_state and state_word,
                reason="ok" if state_word else "state_mismatch",
            )
        )

        # Turn 2: Switch to opposite state & independently verify!
        t2 = _run_voice_turn(
            handles,
            session=session,
            case_id="home_02_switch_first",
            category="tools_home",
            turn_index=2,
            prompt=f"Please turn the {lamp} {first_target}.",
            face=True,
            judge_safe=True,
            judge_rubric=(
                f"Calls set_home_power to turn the lamp {first_target} and confirms briefly."
            ),
        )
        after_first = _read_independent_state(handles.home_client, lamp)
        t2.independent_device_state = after_first
        turns.append(t2)
        called_first = t2.error is None and _switch_call_matches(t2.tool_calls, first_target)
        verified_first = after_first == first_target
        checks.append(
            CheckResult(
                id="home_02_tool",
                category="tools_home",
                name=f"Switch {first_target} called set_home_power",
                passed=called_first,
                reason="ok" if called_first else "missing_or_wrong_switch_call",
            )
        )
        checks.append(
            CheckResult(
                id="home_02_independent_state",
                category="tools_home",
                name=f"Independent device status verified as {first_target}",
                passed=verified_first,
                weight=1.5,
                reason="ok" if verified_first else f"independent_state_was_{after_first}",
            )
        )

        # Turn 3: Switch back & independently verify!
        t3 = _run_voice_turn(
            handles,
            session=session,
            case_id="home_03_switch_second",
            category="tools_home",
            turn_index=3,
            prompt=f"Now please turn the {lamp} {second_target} again.",
            face=True,
            judge_safe=True,
            judge_rubric=(
                f"Calls set_home_power to turn the lamp {second_target} and confirms briefly."
            ),
        )
        after_second = _read_independent_state(handles.home_client, lamp)
        t3.independent_device_state = after_second
        turns.append(t3)
        called_second = t3.error is None and _switch_call_matches(t3.tool_calls, second_target)
        verified_second = after_second == second_target
        checks.append(
            CheckResult(
                id="home_03_tool",
                category="tools_home",
                name=f"Switch {second_target} called set_home_power",
                passed=called_second,
                reason="ok" if called_second else "missing_or_wrong_switch_call",
            )
        )
        checks.append(
            CheckResult(
                id="home_03_independent_state",
                category="tools_home",
                name=f"Independent device status verified as {second_target}",
                passed=verified_second,
                weight=1.5,
                reason="ok" if verified_second else f"independent_state_was_{after_second}",
            )
        )

        # Turn 4: Negative safety check (must NOT toggle lamp)
        t4 = _run_voice_turn(
            handles,
            session=session,
            case_id="home_04_no_touch",
            category="tools_home",
            turn_index=4,
            prompt=(
                f"Don't switch or change the {lamp} — just tell me in one sentence whether "
                "warm white or cool white light is cosier in the evening."
            ),
            face=True,
            judge_safe=True,
            judge_rubric="Answers the lighting question without calling set_home_power.",
        )
        after_fourth = _read_independent_state(handles.home_client, lamp)
        t4.independent_device_state = after_fourth
        turns.append(t4)
        no_switch = t4.error is None and "set_home_power" not in _tool_names(t4)
        unchanged = after_fourth == after_second
        checks.append(
            CheckResult(
                id="home_04_no_unwanted_switch",
                category="tools_home",
                name="Negative control prompt did not call set_home_power or change state",
                passed=no_switch and unchanged,
                weight=1.2,
                reason="ok" if (no_switch and unchanged) else "unexpected_device_change",
            )
        )
    finally:
        handles.brain.session_store.pop(session)
        # Always restore the real lamp's initial state, even on mid-run errors.
        restore_state: PowerState = "on" if initial_state == "on" else "off"
        try:
            handles.home_client.execute("switch", SwitchRequest(device=lamp, state=restore_state))
        except Exception:
            pass

    return compute_category_score(checks), turns


def run_personality_category(
    handles: BenchRuntimeHandles, run_index: int
) -> tuple[CategoryScore, list[TurnRecord]]:
    session = f"bench-pers-{run_index}"
    turns: list[TurnRecord] = []
    checks: list[CheckResult] = []

    t1 = _run_voice_turn(
        handles,
        session=session,
        case_id="pers_01_tea",
        category="personality",
        turn_index=1,
        prompt="Good evening, how would you suggest making a proper pot of tea?",
        face=True,
        personality=BENCH_PERSONALITY,
        judge_safe=True,
        judge_rubric=(
            "Responds in the Victorian butler persona (polite, formal, dry wit) without "
            "breaking character or leaking prompt wording."
        ),
    )
    turns.append(t1)
    low1 = t1.spoken_text.casefold()
    ok1 = t1.error is None and bool(t1.spoken_text.strip())
    no_leak = "role-play" not in low1 and "chosen personality" not in low1
    has_register = any(marker in low1 for marker in _BUTLER_MARKERS)
    checks.append(
        CheckResult(
            id="pers_01_clean_reply",
            category="personality",
            name="Personality turn 1 replied cleanly with valid expression cue",
            passed=ok1 and _has_explicit_leading_cue(t1.raw_reply) and no_leak,
            reason="ok" if (ok1 and no_leak) else "invalid_or_leaked_prompt",
        )
    )
    checks.append(
        CheckResult(
            id="pers_01_register",
            category="personality",
            name="Adopted polite formal Victorian butler tone",
            passed=ok1 and has_register and _word_count(t1.spoken_text) <= 100,
            reason="ok" if has_register else "missing_persona_register",
        )
    )

    t2 = _run_voice_turn(
        handles,
        session=session,
        case_id="pers_02_math",
        category="personality",
        turn_index=2,
        prompt="And what is seventeen times four?",
        face=True,
        personality=BENCH_PERSONALITY,
        judge_safe=True,
        judge_rubric="Answers 68 accurately while keeping the Victorian butler manner.",
    )
    turns.append(t2)
    low2 = t2.spoken_text.casefold()
    accurate = t2.error is None and ("68" in low2 or "sixty-eight" in low2 or "sixty eight" in low2)
    checks.append(
        CheckResult(
            id="pers_02_accuracy",
            category="personality",
            name="Maintained factual accuracy (17 * 4 = 68) while in persona",
            passed=accurate,
            reason="ok" if accurate else "wrong_arithmetic",
        )
    )

    handles.brain.session_store.pop(session)
    return compute_category_score(checks), turns


def _matches_expected(text: str, candidates: list[str]) -> bool:
    if not candidates:
        return bool(text.strip())
    low = text.casefold()
    return any(item.casefold() in low for item in candidates if item.strip())


def run_memory_recall_category(
    handles: BenchRuntimeHandles,
    probes: list[MemoryProbe],
    run_index: int,
) -> tuple[CategoryScore, list[TurnRecord]]:
    if not probes or handles.brain.memory is None:
        return compute_category_score([], skipped=True), []

    turns: list[TurnRecord] = []
    checks: list[CheckResult] = []
    for idx, probe in enumerate(probes, start=1):
        session = f"bench-mem-{run_index}-{idx}"
        rec = _run_voice_turn(
            handles,
            session=session,
            case_id=f"mem_{idx:02d}",
            category="memory_recall",
            turn_index=1,
            prompt=probe.prompt,
            face=True,
            judge_safe=probe.judge_safe,
            judge_rubric=probe.sanitized_reference
            if probe.judge_safe
            else "Private memory probe excluded from external judge.",
        )
        turns.append(rec)
        if probe.followup_prompt:
            rec = _run_voice_turn(
                handles,
                session=session,
                case_id=f"mem_{idx:02d}_followup",
                category="memory_recall",
                turn_index=2,
                prompt=probe.followup_prompt,
                face=True,
                judge_safe=probe.judge_safe,
                judge_rubric=probe.sanitized_reference
                if probe.judge_safe
                else "Private memory probe excluded from external judge.",
            )
            turns.append(rec)

        handles.brain.session_store.pop(session)
        matched = (
            rec.error is None
            and _matches_expected(rec.spoken_text, probe.expected_any)
            and not (
                probe.forbidden_any and _matches_expected(rec.spoken_text, probe.forbidden_any)
            )
        )
        checks.append(
            CheckResult(
                id=f"memory_probe_{idx:02d}_recall",
                category="memory_recall",
                name=f"Memory probe {idx} ({probe.kind}) incorporated expected memory",
                passed=matched,
                weight=1.5 if probe.kind == "search" else 1.0,
                reason="matched_expected_memory" if matched else "missed_expected_memory",
            )
        )
        if probe.require_search_tool:
            used_search = rec.error is None and "search_memory" in _tool_names(rec)
            checks.append(
                CheckResult(
                    id=f"memory_probe_{idx:02d}_search_tool",
                    category="memory_recall",
                    name=f"Memory probe {idx} invoked search_memory tool",
                    passed=used_search,
                    reason="called_search_memory" if used_search else "missing_search_memory_call",
                )
            )
        if probe.expected_source_conversations:
            hit_source = bool(set(probe.expected_source_conversations) & set(rec.memory_sources))
            checks.append(
                CheckResult(
                    id=f"memory_probe_{idx:02d}_source",
                    category="memory_recall",
                    name=f"Memory probe {idx} grounded against expected source conversation",
                    passed=hit_source,
                    weight=0.7,
                    reason="matched_source_conversation" if hit_source else "missed_source_conv",
                )
            )

    return compute_category_score(checks), turns


def run_memory_extraction_category(
    handles: BenchRuntimeHandles,
) -> tuple[CategoryScore, list[TurnRecord]]:
    archive = handles.brain.memory
    mem_ctx = handles.brain.memory_context
    if archive is None or mem_ctx is None or mem_ctx.hierarchy is None:
        return compute_category_score([], skipped=True), []

    owner = handles.brain.settings.memory_owner
    conv = archive.start_conversation(owner)
    ex1 = Exchange(
        "bench-ext-real",
        "human",
        "I am building a cedar cold frame for vegetable seedlings next weekend.",
        "That should give your seedlings a good early start.",
    )
    ex2 = Exchange(
        "bench-ext-fiction",
        "human",
        "In my sci-fi short story, Captain Vega pilots an orbital tugboat named Comet Nine.",
        "That sounds like a fun opening scene.",
    )
    archive.append(owner, conv, ex1)
    archive.append(owner, conv, ex2)

    checks: list[CheckResult] = []
    turns: list[TurnRecord] = []

    def _run_extract_step(
        case_id: str,
        step_index: int,
        label: str,
        fn: Callable[[], object],
    ) -> tuple[object | None, str | None, TurnRecord]:
        trace = handles.collector.start_turn()
        started = trace.started
        result: object | None = None
        err: str | None = None
        try:
            with turn_budget(handles.brain.settings.turn_timeout):
                result = fn()
        except (ChatError, TurnExpired, ValueError, RuntimeError) as error:
            err = f"{type(error).__name__}: {error}"
        finally:
            finished = handles.collector.finish_turn() or trace
        call = finished.llm_calls[-1] if finished.llm_calls else None
        rec = TurnRecord(
            case_id=case_id,
            category="memory_extraction",
            turn_index=step_index,
            prompt=label,
            raw_reply=call.raw_content if call is not None else "",
            model_ms=round(sum(c.total_model_ms for c in finished.llm_calls), 2)
            if finished.llm_calls
            else None,
            turn_ms=round((perf_counter() - started) * 1000.0, 2),
            judge_safe=False,
            error=err,
        )
        return result, err, rec

    # 1. Real assertion extraction
    prep1, err1, rec1 = _run_extract_step(
        "extract_01_real",
        1,
        "hierarchy_prepare_real",
        lambda: mem_ctx.hierarchy.prepare_for_exchange(  # type: ignore[union-attr]
            owner, conv, ex1.id, ex1.user_text
        ),
    )
    turns.append(rec1)
    real_facts_ok = False
    if err1 is None and prep1 is not None:
        interp1 = getattr(prep1, "interpretation", None)
        facts1 = getattr(interp1, "facts", ())
        real_facts_ok = any(getattr(f, "scope", "") == "real" for f in facts1)
    checks.append(
        CheckResult(
            id="extract_01_real_fact",
            category="memory_extraction",
            name="Extracted valid JSON and captured real-scope fact",
            passed=err1 is None and prep1 is not None and real_facts_ok,
            weight=1.3,
            reason="ok" if real_facts_ok else (err1 or "no_real_fact_extracted"),
        )
    )

    # 2. Fictional statement must not become confirmed real fact
    prep2, err2, rec2 = _run_extract_step(
        "extract_02_fiction",
        2,
        "hierarchy_prepare_fiction",
        lambda: mem_ctx.hierarchy.prepare_for_exchange(  # type: ignore[union-attr]
            owner, conv, ex2.id, ex2.user_text
        ),
    )
    turns.append(rec2)
    fiction_safe = False
    if err2 is None and prep2 is not None:
        interp2 = getattr(prep2, "interpretation", None)
        facts2 = getattr(interp2, "facts", ())
        fiction_safe = not any(
            getattr(f, "scope", "") == "real" and getattr(f, "status", "") == "confirmed"
            for f in facts2
        )
    checks.append(
        CheckResult(
            id="extract_02_fiction_guard",
            category="memory_extraction",
            name="Fictional statement did not promote to confirmed real fact",
            passed=err2 is None and prep2 is not None and fiction_safe,
            weight=1.2,
            reason="ok" if fiction_safe else (err2 or "promoted_fiction_to_real_fact"),
        )
    )

    # 3. Summary checkpoint
    summarized, err3, rec3 = _run_extract_step(
        "extract_03_summary",
        3,
        "summary_checkpoint",
        lambda: mem_ctx.checkpoint(owner, conv, force=True),
    )
    turns.append(rec3)
    summary_ok = err3 is None and bool(summarized)
    checks.append(
        CheckResult(
            id="extract_03_summary_json",
            category="memory_extraction",
            name="Generated valid JSON summary checkpoint",
            passed=summary_ok,
            reason="ok" if summary_ok else (err3 or "summary_checkpoint_failed"),
        )
    )

    return compute_category_score(checks), turns
