"""Offline unit and integration tests for Iris Bench."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest

from iris_bench.card import parse_model_card, prompt_for_model_card
from iris_bench.cli import main as bench_main
from iris_bench.judge import (
    _call_openrouter_judge,
    _parse_judge_response,
    build_judge_packet,
    find_pending_run_pairs,
    format_packet_preview,
    judge_runs,
    load_run_pair,
)
from iris_bench.models import PublicBenchmarkResult
from iris_bench.report import (
    format_markdown_table,
    format_terminal_table,
    load_results,
    render_html_report,
)
from iris_bench.runner import assert_public_privacy, prepare_benchmark, run_benchmark
from iris_bench.snapshot import (
    create_synthetic_memory_db,
    draft_memory_probes,
    snapshot_database,
    working_db_copy,
)
from iris_memory import Exchange, SQLiteMemory


def _sse_response(
    content: str | None = None,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    prompt_tokens: int = 40,
    completion_tokens: int = 12,
    predicted_per_second: float = 48.0,
) -> httpx.Response:
    events: list[dict[str, Any]] = []
    if tool_calls:
        for idx, tc in enumerate(tool_calls):
            events.append(
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": idx,
                                        "id": tc["id"],
                                        "type": "function",
                                        "function": {
                                            "name": tc["name"],
                                            "arguments": json.dumps(tc["arguments"]),
                                        },
                                    }
                                ],
                            },
                            "finish_reason": None,
                        }
                    ]
                }
            )
        events.append(
            {
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
                "timings": {
                    "predicted_per_second": predicted_per_second,
                    "prompt_per_second": 220.0,
                },
            }
        )
    else:
        text = content or ""
        mid = max(1, len(text) // 2)
        for chunk in (text[:mid], text[mid:]):
            if chunk:
                events.append(
                    {
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": chunk},
                                "finish_reason": None,
                            }
                        ]
                    }
                )
        events.append(
            {
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
                "timings": {
                    "predicted_per_second": predicted_per_second,
                    "prompt_per_second": 240.0,
                },
            }
        )
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(
        200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"}
    )


class FaithfulIrisMockServer(httpx.BaseTransport):
    """Scripted streaming OpenAI-compatible mock server exercising every Iris Bench scenario."""

    def __init__(self, *, lying_home_model: bool = False, omit_usage: bool = False) -> None:
        self.lying_home_model = lying_home_model
        self.omit_usage = omit_usage
        self.lamp_checked = "off"

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages = payload["messages"]
        system = messages[0]["content"] or ""
        last = messages[-1]
        role = last["role"]
        stream = bool(payload.get("stream", False))

        # Non-streaming memory extraction / summary calls
        if not stream:
            if system.startswith("Extract memory metadata"):
                extract_payload = json.loads(last.get("content") or "{}")
                current_human = str(extract_payload.get("current_human", "")).lower()
                if "cedar cold frame" in current_human:
                    reply_obj = {
                        "facts": [
                            {
                                "key": "garden_project",
                                "value": "cedar cold frame for vegetable seedlings",
                                "kind": "project",
                                "scope": "real",
                                "lifetime": "ongoing",
                                "importance": "normal",
                                "status": "confirmed",
                                "supersedes": None,
                                "evidence": (
                                    "I am building a cedar cold frame for vegetable seedlings "
                                    "next weekend."
                                ),
                            }
                        ],
                        "topics": [],
                    }
                else:
                    reply_obj = {
                        "facts": [
                            {
                                "key": "fiction_character",
                                "value": "Captain Vega pilots an orbital tugboat named Comet Nine",
                                "kind": "episode",
                                "scope": "fictional",
                                "lifetime": "episodic",
                                "importance": "low",
                                "status": "tentative",
                                "supersedes": None,
                                "evidence": (
                                    "In my sci-fi short story, Captain Vega pilots an orbital "
                                    "tugboat named Comet Nine."
                                ),
                            }
                        ],
                        "topics": [],
                    }
            else:
                reply_obj = {"notes": []}
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": json.dumps(reply_obj)},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

        # Tool-result follow-up turns
        if role == "tool":
            prev_assistant = messages[-2]
            tool_name = prev_assistant["tool_calls"][0]["function"]["name"]
            tool_data = json.loads(last["content"])
            if tool_name == "get_local_time":
                reply = (
                    f"[[expression:neutral]]\nIt is {tool_data['weekday']} at {tool_data['time']}."
                )
            elif tool_name == "get_local_weather":
                temp = tool_data["current"]["temperature_2m"]
                cond = tool_data["current"]["conditions"]
                reply = (
                    f"[[expression:curious]]\nRight now it's {temp} degrees and {cond}, "
                    "with rain likely tomorrow (forecast via Open-Meteo)."
                )
            elif tool_name == "get_home_state":
                self.lamp_checked = str(tool_data["state"])
                reply = (
                    f"[[expression:neutral]]\nThe living room lamp is currently "
                    f"{self.lamp_checked}."
                )
            elif tool_name == "set_home_power":
                req_state = tool_data.get("requested_state", "on")
                reply = (
                    f"[[expression:delighted]]\nDone, I've turned the living room lamp {req_state}."
                )
            elif tool_name == "search_memory":
                reply = (
                    "[[expression:curious]]\nFrom your saved archive, we discussed your cedar "
                    "greenhouse irrigation manifold and its brass valves."
                )
            else:
                reply = "[[expression:neutral]]\nAll done."
            return self._respond(reply)

        # First-pass user turns
        user_prompts = [m.get("content") or "" for m in messages if m["role"] == "user"]
        latest_user = user_prompts[-1].lower()

        if "why is the sky blue" in latest_user:
            return self._respond(
                "[[expression:curious]]\nSunlight scatters off air molecules, and shorter blue "
                "wavelengths scatter much more strongly across the sky."
            )
        if "ten years old" in latest_user:
            return self._respond(
                "[[expression:amused]]\nSunlight is a mix of colours, and the air bounces blue "
                "light around like a pinball. That's why looking up shows blue everywhere."
            )
        if "great, thanks, that makes sense" in latest_user:
            return self._respond("[[expression:delighted]]\nYou're very welcome.")
        if "bookshelf made of oak" in latest_user:
            return self._respond(
                "[[expression:focused]]\nLet the oak timber acclimatise indoors for a few "
                "days before cutting."
            )
        if "simple wall brackets" in latest_user:
            return self._respond(
                "[[expression:focused]]\nTwo sturdy steel L-brackets anchored into wall studs "
                "will support it cleanly."
            )
        if "change the wood from oak to pine" in latest_user:
            return self._respond(
                "[[expression:curious]]\nPine is lighter, so standard medium-duty brackets "
                "at 80 centimetres will work nicely."
            )
        if "current time" in latest_user or "weekday, and time" in latest_user:
            return self._tool_respond("call_time", "get_local_time", {})
        if "remind me what wood and width" in latest_user:
            return self._respond(
                "[[expression:focused]]\nWe settled on pine wood at 80 centimetres wide."
            )
        if "all i need on the shelf" in latest_user:
            return self._respond("[[expression:delighted]]\nEnjoy building it!")
        if "passed a really tough exam" in latest_user:
            return self._respond("[[expression:delighted]]\nBrilliant news, well done!")
        if "dropped my favourite mug" in latest_user:
            return self._respond("[[expression:concerned]]\nOh no, I'm sorry to hear that.")
        if "why do mirrors flip" in latest_user:
            return self._respond(
                "[[expression:curious]]\nThey actually flip front-to-back, which we interpret "
                "as left-right."
            )
        if "trust atoms" in latest_user:
            return self._respond("[[expression:amused]]\nHaha, classic one.")
        if "weather like right now" in latest_user:
            return self._tool_respond("call_weather", "get_local_weather", {"days": 2})
        if "check whether the" in latest_user:
            return self._tool_respond(
                "call_home_state", "get_home_state", {"device": "living room lamp"}
            )
        if "please turn the" in latest_user or "now please turn the" in latest_user:
            target = "on" if re.search(r"\bon\b", latest_user) else "off"
            if self.lying_home_model:
                # Simulate an LLM hallucinating that it switched the lamp without calling the tool.
                return self._respond(
                    f"[[expression:delighted]]\nI have switched the living room lamp {target}."
                )
            return self._tool_respond(
                f"call_switch_{target}",
                "set_home_power",
                {"device": "living room lamp", "state": target},
            )
        if "warm white or cool white" in latest_user:
            return self._respond(
                "[[expression:neutral]]\nWarm white light feels much cosier in the evening."
            )
        if "proper pot of tea" in latest_user:
            return self._respond(
                "[[expression:proud]]\nVery good, madam. Warm the pot first and steep loose "
                "leaves for four minutes, indeed."
            )
        if "seventeen times four" in latest_user:
            return self._respond("[[expression:focused]]\nThat would be 68, if I may say so.")
        if "favourite tea" in latest_user:
            return self._respond(
                "[[expression:delighted]]\nYour favourite tea is Earl Grey with lemon."
            )
        if "mirror" in latest_user and "telescope" in latest_user:
            return self._respond(
                "[[expression:curious]]\nYour Dobsonian telescope uses an eight-inch "
                "parabolic mirror."
            )
        if "about the greenhouse" in latest_user:
            return self._tool_respond(
                "call_mem_search", "search_memory", {"query": "greenhouse irrigation"}
            )
        return self._respond("[[expression:neutral]]\nHello there, ready when you are.")

    def _respond(self, text: str) -> httpx.Response:
        if self.omit_usage:
            events = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": text}}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ]
            body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
            return httpx.Response(
                200, content=body.encode(), headers={"content-type": "text/event-stream"}
            )
        return _sse_response(text)

    def _tool_respond(self, call_id: str, name: str, arguments: dict[str, Any]) -> httpx.Response:
        return _sse_response(tool_calls=[{"id": call_id, "name": name, "arguments": arguments}])


def test_snapshot_is_readonly_and_working_copy_is_isolated(tmp_path: Path) -> None:
    live_db = tmp_path / "live.sqlite3"
    archive = SQLiteMemory(live_db)
    conv = archive.start_conversation("default")
    archive.append(
        "default", conv, Exchange("ex-1", "human", "Secret dog name is Barnaby", "Noted.")
    )
    with archive.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "INSERT INTO memory_details(owner,label,text,updated_at) VALUES (?,?,?,?)",
            ("default", "pet", "Barnaby the terrier", "2026-10-01T00:00:00+00:00"),
        )
    before_bytes = live_db.read_bytes()

    snap = tmp_path / "snapshot.sqlite3"
    counts = snapshot_database(live_db, snap)
    assert counts["exchanges"] == 1
    assert counts["memory_details"] == 1
    assert live_db.read_bytes() == before_bytes

    probes = draft_memory_probes(snap, owner="default")
    assert probes.probes
    assert all(p.judge_safe is False for p in probes.probes)

    with working_db_copy(snap, live_db_path=live_db) as working:
        assert working.resolve() != live_db.resolve()
        assert working.resolve() != snap.resolve()
        conn = sqlite3.connect(working)
        conn.execute("DELETE FROM memory_details")
        conn.commit()
        conn.close()

    assert live_db.read_bytes() == before_bytes
    snap_probes_after = draft_memory_probes(snap, owner="default")
    assert len(snap_probes_after.probes) == len(probes.probes)


def test_prepare_preserves_edited_probes(tmp_path: Path) -> None:
    live_db = tmp_path / "live.sqlite3"
    create_synthetic_memory_db(live_db)
    cache_dir = tmp_path / "cache"

    first = prepare_benchmark(db_path=live_db, cache_dir=cache_dir, lamp_device="living room lamp")
    assert first.probe_count >= 1
    assert not first.probes_reused

    probes_path = Path(first.config.probes_path)
    probes_path.write_text(
        probes_path.read_text(encoding="utf-8").replace("favourite tea", "custom edited label"),
        encoding="utf-8",
    )
    second = prepare_benchmark(db_path=live_db, cache_dir=cache_dir)
    assert second.probes_reused
    assert "custom edited label" in probes_path.read_text(encoding="utf-8")


def test_end_to_end_benchmark_and_privacy(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    results_dir = tmp_path / "results"
    server = FaithfulIrisMockServer()

    outcome = run_benchmark(
        "mock-engine 1.0",
        "mock-model-q4",
        warmup_turns=2,
        runs=1,
        mock_tools=True,
        synthetic_memory=True,
        cache_dir=cache_dir,
        results_dir=results_dir,
        client_transport=server,
    )
    res = outcome.result
    assert res.comments == ""
    assert res.speed.metered_turns >= 20
    assert len(res.speed.long_conversation_ttfs_ms) == 6
    assert res.speed.median_ttft_ms is not None
    assert res.speed.median_ttfs_ms is not None
    assert res.speed.median_tok_per_sec == 48.0
    assert res.scores.capability_score == 100.0
    assert res.scores.overall_iris_score > 80.0
    assert res.categories["tools_home"].score == 100.0
    assert res.categories["memory_recall"].score == 100.0
    assert res.categories["memory_extraction"].score == 100.0

    # Ensure public JSON contains zero private prompts/replies/memory values
    raw_public = outcome.public_path.read_text(encoding="utf-8")
    assert "Barnaby" not in raw_public
    assert "Earl Grey" not in raw_public
    assert "Dobsonian" not in raw_public

    with pytest.raises(ValueError, match="Privacy check failed"):
        assert_public_privacy(res, forbidden_terms=["mock-model-q4"])


def test_lying_home_model_is_caught_by_independent_state_check(tmp_path: Path) -> None:
    outcome = run_benchmark(
        "mock-engine 1.0",
        "lying-model",
        warmup_turns=1,
        mock_tools=True,
        synthetic_memory=True,
        cache_dir=tmp_path / "cache",
        results_dir=tmp_path / "results",
        client_transport=FaithfulIrisMockServer(lying_home_model=True),
    )
    home_cat = outcome.result.categories["tools_home"]
    failed_ids = {c.id for c in home_cat.checks if not c.passed}
    assert "home_02_independent_state" in failed_ids
    assert "home_02_tool" in failed_ids
    assert home_cat.score < 100.0


def test_speed_fallback_when_server_omits_usage(tmp_path: Path) -> None:
    outcome = run_benchmark(
        "mock-engine 1.0",
        "no-usage-model",
        warmup_turns=1,
        mock_tools=True,
        synthetic_memory=True,
        cache_dir=tmp_path / "cache",
        results_dir=tmp_path / "results",
        client_transport=FaithfulIrisMockServer(omit_usage=True),
    )
    assert outcome.result.speed.median_tok_per_sec is None
    assert outcome.result.speed.median_est_tok_per_sec is not None


def test_judge_privacy_gate_scrubs_and_requires_approval(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    results_dir = tmp_path / "results"
    outcome = run_benchmark(
        "mock-engine 1.0",
        "judge-test-model",
        warmup_turns=0,
        mock_tools=True,
        synthetic_memory=True,
        cache_dir=cache_dir,
        results_dir=results_dir,
        client_transport=FaithfulIrisMockServer(),
    )
    # Mark one memory turn private (judge_safe=False) & plant a custom location to verify scrubbing
    transcript = outcome.transcript.model_copy(
        update={
            "weather_location_to_scrub": "Stroud",
            "lamp_device_to_scrub": "Secret Study Lamp",
        }
    )
    transcript.turns[0] = transcript.turns[0].model_copy(
        update={"prompt": "Near Stroud near Secret Study Lamp", "judge_safe": True}
    )
    mem_idx = next(i for i, t in enumerate(transcript.turns) if t.category == "memory_recall")
    transcript.turns[mem_idx] = transcript.turns[mem_idx].model_copy(update={"judge_safe": False})
    outcome.transcript_path.write_text(transcript.model_dump_json(indent=2), encoding="utf-8")

    pair = load_run_pair(outcome.result.run_id, cache_dir=cache_dir)
    packet = build_judge_packet(pair.transcript)
    assert packet.excluded_private_count == 1
    assert "Stroud" not in packet.cases[0].prompt
    assert "[configured location]" in packet.cases[0].prompt
    assert "Secret Study Lamp" not in packet.cases[0].prompt
    assert "living room lamp" in packet.cases[0].prompt

    preview = format_packet_preview([packet], "google/gemini-3.8-flash")
    assert "google/gemini-3.8-flash" in preview
    assert "Excluded : 1 private case(s)" in preview

    judge_calls = 0

    def mock_openrouter(request: httpx.Request) -> httpx.Response:
        nonlocal judge_calls
        judge_calls += 1
        req = json.loads(request.content)
        user_cases = json.loads(req["messages"][1]["content"])["cases"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "cases": [
                                        {
                                            "case_id": c["case_id"],
                                            "scores": {"conversational_naturalness": 4.5},
                                            "note": "Clean response.",
                                        }
                                        for c in user_cases
                                    ]
                                }
                            ),
                        }
                    }
                ]
            },
        )

    transport = httpx.MockTransport(mock_openrouter)

    # Declining confirmation must make zero network calls
    cancelled = judge_runs(
        [pair],
        api_key="test-key",
        confirm_fn=lambda pkts, m: False,
        transport=transport,
    )
    assert cancelled == []
    assert judge_calls == 0

    # Batch pending approval sends and updates public JSON
    pending = find_pending_run_pairs(cache_dir=cache_dir)
    assert len(pending) == 1
    approved = judge_runs(
        pending,
        judge_model="google/gemini-3.8-flash",
        api_key="test-key",
        confirm_fn=lambda pkts, m: True,
        transport=transport,
    )
    assert len(approved) == 1
    assert judge_calls == 1
    updated = PublicBenchmarkResult.model_validate_json(
        outcome.public_path.read_text(encoding="utf-8")
    )
    assert updated.judge is not None
    assert updated.judge.judge_model == "google/gemini-3.8-flash"
    assert updated.judge.overall_score == 90.0
    assert updated.scores.judge_quality_score == 90.0


def test_report_generation_and_cli_no_prompt(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    results_dir = tmp_path / "results"
    html_path = tmp_path / "report.html"
    server = FaithfulIrisMockServer()

    run_benchmark(
        "koboldcpp 1.106",
        "model-a",
        warmup_turns=0,
        mock_tools=True,
        synthetic_memory=True,
        comments="Snappy on 8GB VRAM",
        cache_dir=cache_dir,
        results_dir=results_dir,
        client_transport=server,
    )
    run_benchmark(
        "llama.cpp b5000",
        "model-b",
        warmup_turns=0,
        mock_tools=True,
        synthetic_memory=True,
        cache_dir=cache_dir,
        results_dir=results_dir,
        client_transport=server,
    )

    loaded = load_results(results_dir)
    assert len(loaded) == 2
    assert "Snappy on 8GB VRAM" in format_terminal_table(loaded)
    assert "| Engine | Model |" in format_markdown_table(loaded)

    rendered = render_html_report(loaded, html_path)
    html_text = rendered.read_text(encoding="utf-8")
    assert "Iris Bench Comparison Report" in html_text
    assert "Snappy on 8GB VRAM" in html_text
    assert "<script src=" not in html_text

    rc = bench_main(
        [
            "report",
            "--results-dir",
            str(results_dir),
            "--html",
            str(html_path),
            "--markdown",
        ]
    )
    assert rc == 0


def test_parse_and_prompt_model_card() -> None:
    sample_card = """
    Model:           Flint Preview
    Model Version:   UD-Q6_K_XL / GGUF v3 qv2
    Model File:      C:\\Users\\alice\\models\\flint-UD-Q6_K_XL.gguf
    Engine:          PolyStrata
    Engine Version:  0.1.45 (commit 3e164216)
    Endpoint:        http://127.0.0.1:8011/v1 (alias: local-chat, auth: none, ctx: 32768)
    Benchmark Label: polystrata-0.1.45-3e164216_flint-ud-q6_k_xl
    """
    parsed = parse_model_card(sample_card)
    assert parsed.engine == "PolyStrata 0.1.45 (commit 3e164216)"
    assert parsed.model == "Flint Preview (UD-Q6_K_XL / GGUF v3 qv2)"
    assert parsed.model_file == "flint-UD-Q6_K_XL.gguf"
    assert parsed.base_url == "http://127.0.0.1:8011/v1"
    assert parsed.api_model == "local-chat"
    assert parsed.model_context_tokens == 32768
    assert parsed.benchmark_label == "polystrata-0.1.45-3e164216_flint-ud-q6_k_xl"

    pasted_lines = iter([*sample_card.strip().splitlines(), ""])
    pasted = prompt_for_model_card(input_fn=lambda _: next(pasted_lines), print_fn=lambda _: None)
    assert pasted == parsed

    manual_inputs = iter(["", "koboldcpp 1.106", "qwen3-8b"])
    manual = prompt_for_model_card(input_fn=lambda _: next(manual_inputs), print_fn=lambda _: None)
    assert manual.engine == "koboldcpp 1.106"
    assert manual.model == "qwen3-8b"


def test_cli_full_accepts_pasted_card_and_card_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    card_text = (
        "Model:           Flint Preview\n"
        "Model Version:   UD-Q6_K_XL / GGUF v3 qv2\n"
        "Model File:      flint-UD-Q6_K_XL.gguf\n"
        "Engine:          PolyStrata\n"
        "Engine Version:  0.1.45 (commit 3e164216)\n"
        "Endpoint:        http://127.0.0.1:8011/v1 (alias: local-chat, auth: none, ctx: 32768)\n"
        "Benchmark Label: polystrata-0.1.45-3e164216_flint-ud-q6_k_xl\n"
    )
    results_dir = tmp_path / "results"
    cache_dir = tmp_path / "cache"
    html_path = tmp_path / "report.html"
    captured_calls: list[dict[str, Any]] = []

    def fake_run_benchmark(engine: str, model: str, **kwargs: Any) -> Any:
        captured_calls.append({"engine": engine, "model": model, **kwargs})
        return run_benchmark(
            engine,
            model,
            warmup_turns=0,
            mock_tools=True,
            synthetic_memory=True,
            model_context_tokens=kwargs.get("model_context_tokens"),
            model_file=kwargs.get("model_file"),
            benchmark_label=kwargs.get("benchmark_label"),
            cache_dir=kwargs["cache_dir"],
            results_dir=kwargs["results_dir"],
            client_transport=FaithfulIrisMockServer(),
        )

    monkeypatch.setattr("iris_bench.cli.run_benchmark", fake_run_benchmark)
    answers = iter([*card_text.splitlines(), "", "Nice speed on Flint"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))

    rc = bench_main(
        [
            "full",
            "--no-judge",
            "--cache-dir",
            str(cache_dir),
            "--output-dir",
            str(results_dir),
            "--html",
            str(html_path),
        ]
    )
    assert rc == 0
    assert len(captured_calls) == 1
    call = captured_calls[0]
    assert call["engine"] == "PolyStrata 0.1.45 (commit 3e164216)"
    assert call["model"] == "Flint Preview (UD-Q6_K_XL / GGUF v3 qv2)"
    assert call["base_url"] == "http://127.0.0.1:8011/v1"
    assert call["api_model"] == "local-chat"
    assert call["model_context_tokens"] == 32768
    assert call["model_file"] == "flint-UD-Q6_K_XL.gguf"
    assert call["benchmark_label"] == "polystrata-0.1.45-3e164216_flint-ud-q6_k_xl"

    loaded = load_results(results_dir)
    assert len(loaded) == 1
    assert loaded[0].comments == "Nice speed on Flint"
    assert loaded[0].model_file == "flint-UD-Q6_K_XL.gguf"
    assert loaded[0].benchmark_label == "polystrata-0.1.45-3e164216_flint-ud-q6_k_xl"
    assert loaded[0].config.model_context_tokens == 32768
    assert "polystrata-0.1.45-3e164216_flint-ud-q6_k_xl" in loaded[0].run_id

    # Also verify --card file with --no-prompt
    card_file = tmp_path / "card.txt"
    card_file.write_text(card_text, encoding="utf-8")
    rc2 = bench_main(
        [
            "full",
            "--card",
            str(card_file),
            "--no-prompt",
            "--cache-dir",
            str(cache_dir),
            "--output-dir",
            str(results_dir),
            "--html",
            str(html_path),
        ]
    )
    assert rc2 == 0
    assert len(captured_calls) == 2


def test_parse_judge_response_handles_prose_and_extra_fields() -> None:
    prose_wrapped = (
        "Sure! Here is my evaluation:\n"
        "- tool_honesty: 5\n\n"
        "```json\n"
        '{"cases": [{"case_id": "conv_01", "scores": {"conversational_naturalness": 4.5}, '
        '"note": "Great.", "extra_field": "ignored"}], "summary": "ignored"}\n'
        "```\n"
        "Hope that helps!"
    )
    parsed = _parse_judge_response(prose_wrapped)
    assert len(parsed.cases) == 1
    assert parsed.cases[0].case_id == "conv_01"
    assert parsed.cases[0].scores["conversational_naturalness"] == 4.5


def test_judge_repair_fallback_and_batch_resilience(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    results_dir = tmp_path / "results"
    o1 = run_benchmark(
        "mock-engine 1.0",
        "model-one",
        warmup_turns=0,
        mock_tools=True,
        synthetic_memory=True,
        cache_dir=cache_dir,
        results_dir=results_dir,
        client_transport=FaithfulIrisMockServer(),
    )
    o2 = run_benchmark(
        "mock-engine 1.0",
        "model-two",
        warmup_turns=0,
        mock_tools=True,
        synthetic_memory=True,
        cache_dir=cache_dir,
        results_dir=results_dir,
        client_transport=FaithfulIrisMockServer(),
    )

    calls: list[dict[str, Any]] = []

    def repair_transport(request: httpx.Request) -> httpx.Response:
        req = json.loads(request.content)
        calls.append(req)
        if len(calls) == 1 and "response_format" in req:
            # Simulate a provider rejecting response_format with 400
            return httpx.Response(400, json={"error": "unsupported response_format"})
        if len(calls) == 2:
            # Return prose bullets on first attempt so repair pass triggers
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": " and truthfully states it:\n- tool_honesty: 5",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "cases": [
                                        {
                                            "case_id": "conv_01",
                                            "scores": {"conversational_naturalness": 4.0},
                                            "note": "Repaired JSON.",
                                        }
                                    ]
                                }
                            ),
                        }
                    }
                ]
            },
        )

    p1 = load_run_pair(o1.result.run_id, cache_dir=cache_dir)
    repaired = _call_openrouter_judge(
        build_judge_packet(p1.transcript),
        judge_model="google/gemini-3.8-flash",
        api_key="test-key",
        transport=httpx.MockTransport(repair_transport),
        cache_dir=cache_dir,
    )
    assert len(calls) == 3
    assert repaired.cases[0].case_id == "conv_01"

    # Batch judging: run 1 fails permanently, run 2 still succeeds and gets saved.
    attempt = 0

    def partial_batch_transport(request: httpx.Request) -> httpx.Response:
        nonlocal attempt
        attempt += 1
        if attempt <= 2:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "not json"}}]},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                {
                                    "cases": [
                                        {
                                            "case_id": "conv_01",
                                            "scores": {"conversational_naturalness": 5.0},
                                            "note": "Good.",
                                        }
                                    ]
                                }
                            ),
                        }
                    }
                ]
            },
        )

    errors: list[tuple[str, str]] = []
    outcomes = judge_runs(
        find_pending_run_pairs(cache_dir=cache_dir),
        api_key="test-key",
        auto_approve=True,
        transport=httpx.MockTransport(partial_batch_transport),
        errors_out=errors,
        cache_dir=cache_dir,
    )
    assert len(errors) == 1
    assert errors[0][0] == o1.result.run_id
    assert len(outcomes) == 1
    assert outcomes[0].run_id == o2.result.run_id
    assert (cache_dir / "last_judge_error.txt").is_file()


def test_run_benchmark_saves_partial_on_category_error_and_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_dir = tmp_path / "cache"
    results_dir = tmp_path / "results"

    def explode_home(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("home service crashed")

    monkeypatch.setattr("iris_bench.runner.run_home_category", explode_home)
    outcome = run_benchmark(
        "mock-engine 1.0",
        "resilient-model",
        warmup_turns=0,
        mock_tools=True,
        synthetic_memory=True,
        cache_dir=cache_dir,
        results_dir=results_dir,
        client_transport=FaithfulIrisMockServer(),
    )
    assert outcome.public_path.is_file()
    assert outcome.transcript_path.is_file()
    assert "category_error:tools_home:RuntimeError" in outcome.result.warnings
    assert outcome.result.categories["conversation"].score == 100.0
    assert outcome.result.categories["tools_home"].score == 0.0
    assert outcome.result.categories["memory_extraction"].score == 100.0

    # Also test KeyboardInterrupt mid-suite saves completed categories cleanly.
    def interrupt_expr(*_args: Any, **_kwargs: Any) -> Any:
        raise KeyboardInterrupt()

    monkeypatch.setattr("iris_bench.runner.run_expressions_category", interrupt_expr)
    partial = run_benchmark(
        "mock-engine 1.0",
        "interrupted-model",
        warmup_turns=0,
        mock_tools=True,
        synthetic_memory=True,
        cache_dir=cache_dir,
        results_dir=results_dir,
        client_transport=FaithfulIrisMockServer(),
    )
    assert partial.public_path.is_file()
    assert "interrupted_partial_run" in partial.result.warnings
    assert "conversation" in partial.result.categories
    assert "long_conversation" in partial.result.categories
    assert "expressions" not in partial.result.categories


def test_cli_full_saves_outputs_when_judge_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cache_dir = tmp_path / "cache"
    results_dir = tmp_path / "results"
    html_path = tmp_path / "report.html"

    def fake_run_benchmark(engine: str, model: str, **kwargs: Any) -> Any:
        return run_benchmark(
            engine,
            model,
            warmup_turns=0,
            mock_tools=True,
            synthetic_memory=True,
            cache_dir=kwargs["cache_dir"],
            results_dir=kwargs["results_dir"],
            client_transport=FaithfulIrisMockServer(),
        )

    def failing_judge(pairs: Any, **kwargs: Any) -> Any:
        errors_out = kwargs.get("errors_out")
        if errors_out is not None:
            errors_out.append((pairs[0].transcript.run_id, "ValueError: OpenRouter timeout"))
        return []

    monkeypatch.setattr("iris_bench.cli.run_benchmark", fake_run_benchmark)
    monkeypatch.setattr("iris_bench.cli.judge_runs", failing_judge)
    monkeypatch.setattr("builtins.input", lambda _: "Kept comment despite judge failure")

    rc = bench_main(
        [
            "full",
            "mock-engine",
            "mock-model",
            "--cache-dir",
            str(cache_dir),
            "--output-dir",
            str(results_dir),
            "--html",
            str(html_path),
        ]
    )
    assert rc == 0
    assert html_path.is_file()
    loaded = load_results(results_dir)
    assert len(loaded) == 1
    assert loaded[0].comments == "Kept comment despite judge failure"
    out = capsys.readouterr().out
    assert "Errors / Warnings encountered" in out
    assert "OpenRouter timeout" in out
    assert "iris-bench judge" in out
