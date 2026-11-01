"""Model API adapters. The conversation loop and skills depend only on ChatBackend."""

from __future__ import annotations

import json
import time
from typing import Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from iris_chat_engines.messages import ChatError, Message
from iris_settings import Settings


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


class ChatResponse(BaseModel):
    choices: list[Choice] = Field(min_length=1)


class ChatCompletionsBackend:
    """OpenAI-compatible Chat Completions for local hosts or hosted services."""

    def __init__(self, settings: Settings, client: httpx.Client) -> None:
        self.settings = settings
        self.client = client

    def complete(self, messages: list[Message], tools: list[dict[str, object]]) -> Message:
        payload: dict[str, object] = {
            "model": self.settings.model,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        payload.update(self.settings.chat_request_options)
        if self.settings.chat_template_kwargs:
            payload["chat_template_kwargs"] = self.settings.chat_template_kwargs
        if self.settings.trace:
            print("Sending:", json.dumps(payload, ensure_ascii=False))
        headers = {}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key.get_secret_value()}"
        started = time.monotonic()
        try:
            response = self.client.post(
                f"{self.settings.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(self.settings.timeout, connect=10),
            )
            response.raise_for_status()
            reply = ChatResponse.model_validate_json(response.content).choices[0].message
        except httpx.TimeoutException as error:
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


def make_backend(settings: Settings, client: httpx.Client) -> ChatBackend:
    """Select the API adapter here when adding another provider protocol."""
    return ChatCompletionsBackend(settings, client)
