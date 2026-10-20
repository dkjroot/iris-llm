"""The small chat API shared by KoboldCPP, llama.cpp and other local servers."""

from __future__ import annotations

import html
import json
import time
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from iris_settings import Settings


class ChatError(RuntimeError):
    """A failed turn which leaves the conversation history alone."""


class Message(BaseModel):
    model_config = ConfigDict(strict=True)
    role: Literal["system", "user", "assistant"]
    content: str


class Choice(BaseModel):
    message: Message


class ChatResponse(BaseModel):
    choices: list[Choice] = Field(min_length=1)


class ChatEngine:
    def __init__(self, settings: Settings, client: httpx.Client) -> None:
        self.settings = settings
        self.client = client
        self.history: list[Message] = []

    def get_response(self, user_text: str) -> str:
        if not user_text.strip():
            raise ChatError("There isn't any text to send")
        messages = [
            Message(role="system", content=self.settings.system_prompt),
            *self.history,
            Message(role="user", content=user_text),
        ]
        payload = {
            "model": self.settings.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
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
        text = reply.content
        # Modern chat APIs return plain text. Only decode for a server explicitly
        # configured to escape HTML; otherwise an explanation of &#x27; must survive.
        passes = {"text": 0, "html": 1, "double-html": 2}[self.settings.reply_encoding]
        for _ in range(passes):
            text = html.unescape(text)
        if reply.role != "assistant" or not text.strip():
            raise ChatError("The server returned an empty or invalid assistant reply")
        if self.settings.trace:
            print(f"Received ({time.monotonic() - started:.2f}s):", text)
        self.history.extend([messages[-1], Message(role="assistant", content=text)])
        limit = self.settings.history_turns * 2
        self.history = self.history[-limit:] if limit else []
        return text
