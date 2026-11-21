"""Model API adapters. The conversation loop and skills depend only on ChatBackend."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from iris_chat_engines.messages import ChatError, Message
from iris_domain.messages import ToolCall
from iris_settings import Settings
from iris_turn import check_deadline, measured, remaining_timeout


@dataclass(frozen=True)
class BackendCapabilities:
    tools: bool = True
    completion_reason: bool = False
    streaming: bool = False


def capabilities(backend: object) -> BackendCapabilities:
    """Older and simple fake adapters only need complete()."""
    value = getattr(backend, "capabilities", None)
    return value if isinstance(value, BackendCapabilities) else BackendCapabilities()


class ChatBackend(Protocol):
    def complete(self, messages: list[Message], tools: list[dict[str, object]]) -> Message:
        """Return one assistant message, translating the provider's format as needed.

        Preserve tool IDs and any reasoning required by the provider on subsequent
        calls. Raise ChatError for transport/format errors. Don't mutate messages.
        HTTP clients and authentication belong to the adapter; skills receive no
        provider-specific messages or credentials.
        """
        ...


class Choice(BaseModel):
    message: Message
    finish_reason: str | None = None


class ChatResponse(BaseModel):
    choices: list[Choice] = Field(min_length=1)


def chat_headers(settings: Settings) -> dict[str, str]:
    key = settings.api_key.get_secret_value().strip() if settings.api_key else ""
    if settings.uses_openrouter and not key:
        raise ChatError("Set IRIS_API_KEY to your OpenRouter API key on the Iris server.")
    return {"Authorization": f"Bearer {key}"} if key else {}


class ChatCompletionsBackend:
    """OpenAI-compatible Chat Completions for local hosts or hosted services."""

    capabilities = BackendCapabilities(completion_reason=True, streaming=True)

    def __init__(self, settings: Settings, client: httpx.Client) -> None:
        self.settings = settings
        self.client = client

    def _payload(
        self, messages: list[Message], tools: list[dict[str, object]], *, stream: bool
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.settings.model,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        payload.update(self.settings.chat_request_options)
        if self.settings.chat_template_kwargs and not self.settings.uses_openrouter:
            payload["chat_template_kwargs"] = self.settings.chat_template_kwargs
        return payload

    @measured("model")
    def complete(self, messages: list[Message], tools: list[dict[str, object]]) -> Message:
        headers = chat_headers(self.settings)
        check_deadline()
        payload = self._payload(messages, tools, stream=False)
        if self.settings.trace:
            print("Sending:", json.dumps(payload, ensure_ascii=False))
        started = time.monotonic()
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
            choice = ChatResponse.model_validate_json(response.content).choices[0]
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
        except ValidationError as error:
            raise ChatError(
                "The server didn't return a text chat reply in the expected format."
            ) from error
        if self.settings.trace:
            print(
                f"Received ({time.monotonic() - started:.2f}s):",
                reply.model_dump(exclude_none=True),
            )
        if tools and not reply.tool_calls and reply.content and "<|tool_call>" in reply.content:
            raise ChatError(
                "The model returned raw tool syntax. Check the host's parser and chat template "
                "(for example, Use Jinja on KoboldCPP)."
            )
        return reply

    @measured("model")
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
        if self.settings.trace:
            print("Sending:", json.dumps(payload, ensure_ascii=False))
        started = time.monotonic()
        role = "assistant"
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        reasoning_key: str | None = None
        tool_deltas: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
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
                    choice = ChatResponse.model_validate_json(body).choices[0]
                    reply = choice.message
                    reply._finish_reason = choice.finish_reason
                    check_deadline()
                    if on_text is not None and reply.content and not reply.tool_calls:
                        on_text(reply.content)
                else:
                    for raw_line in response.iter_lines():
                        check_deadline()
                        if should_stop is not None and should_stop():
                            break
                        line = raw_line.strip()
                        if not line or line.startswith(":"):
                            continue
                        if not line.startswith("data:"):
                            continue
                        data_str = line[5:].strip()
                        if data_str == "[DONE]":
                            break
                        event: Any = json.loads(data_str)
                        if not isinstance(event, dict):
                            raise ValueError("Invalid SSE event")
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
                                reasoning_key = reasoning_key or rkey
                                reasoning_parts.append(rval)
                        delta_tools = delta.get("tool_calls")
                        if isinstance(delta_tools, list):
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
        if self.settings.trace:
            print(
                f"Received ({time.monotonic() - started:.2f}s):",
                reply.model_dump(exclude_none=True),
            )
        if tools and not reply.tool_calls and reply.content and "<|tool_call>" in reply.content:
            raise ChatError(
                "The model returned raw tool syntax. Check the host's parser and chat template "
                "(for example, Use Jinja on KoboldCPP)."
            )
        return reply


def make_backend(settings: Settings, client: httpx.Client) -> ChatBackend:
    """Select the API adapter here when adding another provider protocol."""
    return ChatCompletionsBackend(settings, client)
