"""Streaming LLM telemetry, silent STT/TTS sinks, tool instrumentation and offline mocks."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import perf_counter
from typing import Any
from weakref import WeakSet

import httpx
import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ValidationError
from speech_recognition import AudioData

from iris_bench.models import LLMCallTelemetry, ToolCallRecord
from iris_chat_engines.backends import (
    BackendCapabilities,
    ChatCompletionsBackend,
    ChatResponse,
    chat_headers,
)
from iris_chat_engines.messages import ChatError, Message
from iris_domain.expression import strip_thinking_tags
from iris_domain.messages import ToolCall
from iris_home.protocol import DeviceRequest, MeterRequest, ScheduleRequest, SwitchRequest
from iris_settings import Settings
from iris_tools import ToolRegistry
from iris_turn import check_deadline, remaining_timeout


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


@dataclass
class TurnTraceState:
    """Mutable per-turn trace populated across model, tool and TTS callbacks."""

    started: float = field(default_factory=perf_counter)
    first_sentence_at: float | None = None
    sentences: list[str] = field(default_factory=list)
    llm_calls: list[LLMCallTelemetry] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


class TurnTelemetryCollector:
    """Thread-safe collector for streaming TTFT/TTFS, token rates and tool calls."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._current: TurnTraceState | None = None

    def start_turn(self) -> TurnTraceState:
        state = TurnTraceState(started=perf_counter())
        with self._lock:
            self._current = state
        return state

    def finish_turn(self) -> TurnTraceState | None:
        with self._lock:
            state = self._current
            self._current = None
            return state

    def record_llm_call(self, item: LLMCallTelemetry) -> None:
        with self._lock:
            if self._current is not None:
                self._current.llm_calls.append(item)

    def record_tool_call(self, record: ToolCallRecord) -> None:
        with self._lock:
            if self._current is not None:
                self._current.tool_calls.append(record)

    def record_sentence(self, sentence: str) -> None:
        now = perf_counter()
        with self._lock:
            if self._current is not None:
                if self._current.first_sentence_at is None:
                    self._current.first_sentence_at = now
                self._current.sentences.append(sentence)


def _extract_usage_and_timings(
    event: dict[str, Any],
    prompt_tokens: int | None,
    completion_tokens: int | None,
    pred_tps: float | None,
    prompt_tps: float | None,
) -> tuple[int | None, int | None, float | None, float | None]:
    usage = event.get("usage")
    if isinstance(usage, dict):
        pt = _as_int(usage.get("prompt_tokens"))
        ct = _as_int(usage.get("completion_tokens"))
        if pt is not None:
            prompt_tokens = pt
        if ct is not None:
            completion_tokens = ct
    timings = event.get("timings")
    if isinstance(timings, dict):
        pn = _as_int(timings.get("prompt_n"))
        cn = _as_int(timings.get("predicted_n"))
        if prompt_tokens is None and pn is not None:
            prompt_tokens = pn
        if completion_tokens is None and cn is not None:
            completion_tokens = cn
        tps = _as_float(timings.get("predicted_per_second"))
        ptps = _as_float(timings.get("prompt_per_second"))
        if tps is not None and tps > 0:
            pred_tps = tps
        if ptps is not None and ptps > 0:
            prompt_tps = ptps
    return prompt_tokens, completion_tokens, pred_tps, prompt_tps


def _build_llm_telemetry(
    *,
    streamed: bool,
    started: float,
    first_token_at: float | None,
    first_content_at: float | None,
    ended: float,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    server_pred_tps: float | None,
    server_prompt_tps: float | None,
    raw_content: str,
    reasoning_chars: int = 0,
    tool_calls_count: int,
    finish_reason: str | None,
) -> LLMCallTelemetry:
    total_ms = max(0.001, (ended - started) * 1000.0)
    ttft_ms = round((first_token_at - started) * 1000.0, 2) if first_token_at is not None else None
    first_content_ms = (
        round((first_content_at - started) * 1000.0, 2) if first_content_at is not None else None
    )
    if first_token_at is not None and ended > first_token_at:
        decode_seconds = ended - first_token_at
    else:
        decode_seconds = max(0.001, ended - started)

    tokens_per_sec: float | None = None
    if server_pred_tps is not None and server_pred_tps > 0:
        tokens_per_sec = round(server_pred_tps, 2)
    elif completion_tokens is not None and completion_tokens > 0 and decode_seconds > 0:
        tokens_per_sec = round(completion_tokens / decode_seconds, 2)

    est_tokens = max(1.0, len(raw_content) / 4.0) if raw_content else 0.0
    est_tokens_per_sec = (
        round(est_tokens / decode_seconds, 2) if est_tokens > 0 and decode_seconds > 0 else None
    )

    prompt_tokens_per_sec = round(server_prompt_tps, 2) if server_prompt_tps else None
    return LLMCallTelemetry(
        streamed=streamed,
        ttft_ms=ttft_ms,
        first_content_ms=first_content_ms,
        total_model_ms=round(total_ms, 2),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        tokens_per_sec=tokens_per_sec,
        est_tokens_per_sec=est_tokens_per_sec,
        prompt_tokens_per_sec=prompt_tokens_per_sec,
        output_chars=len(raw_content),
        reasoning_chars=reasoning_chars,
        finish_reason=finish_reason,
        tool_calls_count=tool_calls_count,
        raw_content=raw_content,
    )


class MeteredChatCompletionsBackend(ChatCompletionsBackend):
    """Subclass of ChatCompletionsBackend recording TTFT, token rates and raw content."""

    capabilities = BackendCapabilities(completion_reason=True, streaming=True)

    def __init__(
        self,
        settings: Settings,
        client: httpx.Client,
        collector: TurnTelemetryCollector,
    ) -> None:
        super().__init__(settings, client)
        self.collector = collector

    def complete(self, messages: list[Message], tools: list[dict[str, object]]) -> Message:
        headers = chat_headers(self.settings)
        check_deadline()
        payload = self._payload(messages, tools, stream=False)
        started = perf_counter()
        try:
            response = self.client.post(
                f"{self.settings.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(
                    remaining_timeout(self.settings.timeout), connect=remaining_timeout(10)
                ),
            )
            response.raise_for_status()
            raw_json = json.loads(response.content)
            choice = ChatResponse.model_validate(raw_json).choices[0]
            reply = choice.message
            reply._finish_reason = choice.finish_reason
            check_deadline()
        except httpx.TimeoutException as error:
            check_deadline()
            raise ChatError(
                "The model took too long to reply. Check the server or timeout setting."
            ) from error
        except httpx.HTTPStatusError as error:
            raise ChatError(
                f"The chat server returned HTTP {error.response.status_code}. "
                "Check its model, API settings and console."
            ) from error
        except httpx.RequestError as error:
            raise ChatError(
                "Couldn't reach the chat server. Is it running at the configured address?"
            ) from error
        except (ValidationError, ValueError) as error:
            raise ChatError(
                "The server didn't return a text chat reply in the expected format."
            ) from error
        ended = perf_counter()
        pt, ct, pred_tps, prompt_tps = _extract_usage_and_timings(
            raw_json if isinstance(raw_json, dict) else {}, None, None, None, None
        )
        raw_content = reply.content or ""
        inline_think_chars = max(0, len(raw_content) - len(strip_thinking_tags(raw_content)))
        self.collector.record_llm_call(
            _build_llm_telemetry(
                streamed=False,
                started=started,
                first_token_at=None,
                first_content_at=None,
                ended=ended,
                prompt_tokens=pt,
                completion_tokens=ct,
                server_pred_tps=pred_tps,
                server_prompt_tps=prompt_tps,
                raw_content=raw_content,
                reasoning_chars=len(reply.reasoning_content or "") + inline_think_chars,
                tool_calls_count=len(reply.tool_calls or ()),
                finish_reason=choice.finish_reason,
            )
        )
        if tools and not reply.tool_calls and reply.content and "<|tool_call>" in reply.content:
            raise ChatError(
                "The model returned raw tool syntax. Check the host's parser and chat template "
                "(for example, Use Jinja on KoboldCPP)."
            )
        return reply

    def complete_stream(
        self,
        messages: list[Message],
        tools: list[dict[str, object]],
        *,
        on_text: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> Message:
        headers = chat_headers(self.settings)
        check_deadline()
        payload = self._payload(messages, tools, stream=True)
        started = perf_counter()
        first_token_at: float | None = None
        first_content_at: float | None = None
        role = "assistant"
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_key: str | None = None
        tool_deltas: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        server_pred_tps: float | None = None
        server_prompt_tps: float | None = None
        try:
            with self.client.stream(
                "POST",
                f"{self.settings.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(
                    remaining_timeout(self.settings.timeout), connect=remaining_timeout(10)
                ),
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "text/event-stream" not in content_type:
                    body = response.read()
                    raw_json = json.loads(body)
                    choice = ChatResponse.model_validate(raw_json).choices[0]
                    reply = choice.message
                    finish_reason = choice.finish_reason
                    reply._finish_reason = finish_reason
                    check_deadline()
                    prompt_tokens, completion_tokens, server_pred_tps, server_prompt_tps = (
                        _extract_usage_and_timings(
                            raw_json if isinstance(raw_json, dict) else {},
                            prompt_tokens,
                            completion_tokens,
                            server_pred_tps,
                            server_prompt_tps,
                        )
                    )
                    if reply.content or reply.tool_calls:
                        first_token_at = perf_counter()
                    if reply.content:
                        first_content_at = first_token_at
                    if on_text is not None and reply.content and not reply.tool_calls:
                        on_text(reply.content)
                else:
                    for raw_line in response.iter_lines():
                        check_deadline()
                        if should_stop is not None and should_stop():
                            break
                        line = raw_line.strip()
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        event: Any = json.loads(data_str)
                        if not isinstance(event, dict):
                            raise ValueError("Invalid SSE event")
                        prompt_tokens, completion_tokens, server_pred_tps, server_prompt_tps = (
                            _extract_usage_and_timings(
                                event,
                                prompt_tokens,
                                completion_tokens,
                                server_pred_tps,
                                server_prompt_tps,
                            )
                        )
                        choices = event.get("choices")
                        if not isinstance(choices, list) or not choices:
                            continue
                        first = choices[0]
                        if not isinstance(first, dict):
                            continue
                        if isinstance(first.get("finish_reason"), str):
                            finish_reason = first["finish_reason"]
                        delta = first.get("delta")
                        if not isinstance(delta, dict):
                            continue
                        if isinstance(delta.get("role"), str):
                            role = delta["role"]
                        for rkey in ("reasoning_content", "reasoning"):
                            rval = delta.get(rkey)
                            if isinstance(rval, str) and rval:
                                if first_token_at is None:
                                    first_token_at = perf_counter()
                                reasoning_key = reasoning_key or rkey
                                reasoning_parts.append(rval)
                        delta_tools = delta.get("tool_calls")
                        if isinstance(delta_tools, list):
                            if delta_tools and first_token_at is None:
                                first_token_at = perf_counter()
                            for item in delta_tools:
                                if not isinstance(item, dict):
                                    continue
                                idx = int(item.get("index", len(tool_deltas)))
                                slot = tool_deltas.setdefault(
                                    idx, {"id": "", "type": "function", "name": "", "arguments": ""}
                                )
                                if isinstance(item.get("id"), str):
                                    slot["id"] += item["id"]
                                if isinstance(item.get("type"), str):
                                    slot["type"] = item["type"]
                                fn = item.get("function")
                                if isinstance(fn, dict):
                                    if isinstance(fn.get("name"), str):
                                        slot["name"] += fn["name"]
                                    if isinstance(fn.get("arguments"), str):
                                        slot["arguments"] += fn["arguments"]
                        piece = delta.get("content")
                        if isinstance(piece, str) and piece:
                            now = perf_counter()
                            if first_token_at is None:
                                first_token_at = now
                            if first_content_at is None:
                                first_content_at = now
                            content_parts.append(piece)
                            if on_text is not None and not tool_deltas:
                                on_text(piece)
                    assembled_tools: list[ToolCall] | None = None
                    if tool_deltas:
                        assembled_tools = [
                            ToolCall.model_validate(
                                {
                                    "id": slot["id"] or f"call_{idx}",
                                    "type": slot["type"] or "function",
                                    "function": {
                                        "name": slot["name"],
                                        "arguments": slot["arguments"],
                                    },
                                }
                            )
                            for idx, slot in sorted(tool_deltas.items())
                        ]
                    extra: dict[str, object] = {}
                    if reasoning_key and reasoning_parts:
                        extra[reasoning_key] = "".join(reasoning_parts)
                    reply = Message.model_validate(
                        {
                            "role": role,
                            "content": "".join(content_parts) if content_parts else None,
                            "tool_calls": assembled_tools,
                            **extra,
                        }
                    )
                    reply._finish_reason = finish_reason
                    check_deadline()
        except httpx.TimeoutException as error:
            check_deadline()
            raise ChatError(
                "The model took too long to reply. Check the server or timeout setting."
            ) from error
        except httpx.HTTPStatusError as error:
            raise ChatError(
                f"The chat server returned HTTP {error.response.status_code}. "
                "Check its model, API settings and console."
            ) from error
        except httpx.RequestError as error:
            raise ChatError(
                "Couldn't reach the chat server. Is it running at the configured address?"
            ) from error
        except (ValidationError, ValueError) as error:
            raise ChatError(
                "The server didn't return a text chat reply in the expected format."
            ) from error
        ended = perf_counter()
        raw_content = reply.content or ""
        inline_think_chars = max(0, len(raw_content) - len(strip_thinking_tags(raw_content)))
        self.collector.record_llm_call(
            _build_llm_telemetry(
                streamed=True,
                started=started,
                first_token_at=first_token_at,
                first_content_at=first_content_at,
                ended=ended,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                server_pred_tps=server_pred_tps,
                server_prompt_tps=server_prompt_tps,
                raw_content=raw_content,
                reasoning_chars=len(reply.reasoning_content or "") + inline_think_chars,
                tool_calls_count=len(reply.tool_calls or ()),
                finish_reason=finish_reason,
            )
        )
        if tools and not reply.tool_calls and reply.content and "<|tool_call>" in reply.content:
            raise ChatError(
                "The model returned raw tool syntax. Check the host's parser and chat template "
                "(for example, Use Jinja on KoboldCPP)."
            )
        return reply


class BenchSTT:
    """Silent ISTT stand-in for automated text-fed voice turns."""

    def __init__(self, text: str = "") -> None:
        self.text = text

    def recognize(self, audio: AudioData) -> str:
        return self.text

    def is_offline(self) -> bool:
        return True


class BenchStreamingTTS:
    """Silent StreamingSynthesizer capturing sentence emission timestamps (TTFS)."""

    sample_rate: int = 24000

    def __init__(self, collector: TurnTelemetryCollector) -> None:
        self._collector = collector

    def synthesize(self, text: str) -> tuple[npt.NDArray[np.float32], int]:
        self._collector.record_sentence(text)
        return np.zeros(240, dtype=np.float32), self.sample_rate

    def stream(self, text: str) -> Generator[bytes]:
        self._collector.record_sentence(text)
        yield b"\x00\x00" * 120


class BenchMockHomeClient:
    """In-memory HomeClient replacement for --mock-tools offline runs."""

    def __init__(self, device_name: str = "living room lamp") -> None:
        self.device_name = device_name
        self.devices: dict[str, str] = {device_name: "off"}
        self._canonical: dict[str, str] = {device_name.casefold(): device_name}

    def close(self) -> None:
        return None

    def execute(self, operation: str, args: BaseModel) -> dict[str, object]:
        if operation == "list":
            return {
                "scheduler": {"status": "ok"},
                "devices": [{"device": name, "meters": ["power"]} for name in self.devices],
            }
        if isinstance(args, DeviceRequest):
            resolved = self._canonical.get(args.device.strip().casefold())
            if resolved is None:
                return {"error": "Unknown device. List the configured home devices."}
            if isinstance(args, SwitchRequest):
                self.devices[resolved] = args.state
                return {"device": resolved, "accepted": True, "requested_state": args.state}
            if isinstance(args, MeterRequest):
                watts = 7.5 if self.devices[resolved] == "on" else 0.0
                return {"device": resolved, "metric": args.metric, "value": watts, "unit": "W"}
            if isinstance(args, ScheduleRequest):
                return {"device": resolved, "revision": args.revision + 1, "rules": []}
            if operation == "schedule":
                return {
                    "device": resolved,
                    "revision": 1,
                    "timezone": "Europe/London",
                    "rules": [],
                }
            return {"device": resolved, "state": self.devices[resolved]}
        return {"error": f"Unsupported home operation: {operation}"}


class MockWeatherTransport(httpx.BaseTransport):
    """Intercepts Open-Meteo HTTP calls in --mock-tools mode while forwarding LLM calls."""

    def __init__(self, inner: httpx.BaseTransport, *, location_name: str = "Stroud") -> None:
        self._inner = inner
        self._location_name = location_name

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = (request.url.host or "").lower()
        if host == "geocoding-api.open-meteo.com":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": self._location_name,
                            "latitude": 51.745,
                            "longitude": -2.215,
                            "country_code": "GB",
                            "admin1": "England",
                            "admin2": "Gloucestershire",
                        }
                    ]
                },
            )
        if host == "api.open-meteo.com":
            today = datetime.now(UTC).date()
            days = int(request.url.params.get("forecast_days", "3"))
            dates = [(today + timedelta(days=offset)).isoformat() for offset in range(days)]
            return httpx.Response(
                200,
                json={
                    "current": {
                        "time": f"{dates[0]}T12:00",
                        "temperature_2m": 13.4,
                        "apparent_temperature": 11.8,
                        "weather_code": 2,
                        "wind_speed_10m": 8.5,
                    },
                    "daily": {
                        "time": dates,
                        "weather_code": [2 if i == 0 else 61 for i in range(days)],
                        "temperature_2m_max": [15.0 + i for i in range(days)],
                        "temperature_2m_min": [7.0 + i for i in range(days)],
                        "precipitation_probability_max": [
                            20.0 if i == 0 else 75.0 for i in range(days)
                        ],
                        "precipitation_sum": [0.0 if i == 0 else 4.2 for i in range(days)],
                        "wind_speed_10m_max": [12.0 for _ in range(days)],
                    },
                },
            )
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


_INSTRUMENTED_REGISTRIES: WeakSet[ToolRegistry] = WeakSet()


def instrument_tool_registry(registry: ToolRegistry, collector: TurnTelemetryCollector) -> None:
    """Wrap ToolRegistry.execute once so every tool invocation is timed and logged."""
    if registry in _INSTRUMENTED_REGISTRIES:
        return
    original = registry.execute

    def wrapped(name: str, arguments: str) -> str:
        started = time.perf_counter()
        raw_result = original(name, arguments)
        duration_ms = round((time.perf_counter() - started) * 1000.0, 2)
        ok = True
        try:
            parsed = json.loads(raw_result)
            if isinstance(parsed, dict) and "error" in parsed:
                ok = False
        except ValueError:
            ok = False
        collector.record_tool_call(
            ToolCallRecord(
                name=name,
                arguments=arguments,
                result_summary_ok=ok,
                duration_ms=duration_ms,
                raw_result=raw_result,
            )
        )
        return raw_result

    registry.execute = wrapped  # type: ignore[method-assign]
    _INSTRUMENTED_REGISTRIES.add(registry)
