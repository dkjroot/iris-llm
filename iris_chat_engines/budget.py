"""Conservative prompt sizing until adapters expose their model's tokenizer."""

from __future__ import annotations

import json

from iris_chat_engines.messages import ChatError, Message
from iris_settings import Settings


def estimate(messages: list[Message], tools: list[dict[str, object]]) -> int:
    # One token per UTF-8 byte intentionally overestimates normal prose. Count
    # serialised tool schemas, arguments and reasoning too, plus message framing.
    payload = {"messages": [m.model_dump(exclude_none=True) for m in messages], "tools": tools}
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 32 * len(messages)


def input_budget(settings: Settings) -> int:
    return settings.model_context_tokens - settings.max_tokens - settings.context_margin_tokens


def fit(
    settings: Settings,
    system: str,
    history: list[Message],
    event: Message,
    tools: list[dict[str, object]],
    memory: str = "",
) -> list[Message]:
    """Keep whole recent turns, then remove derived memory if even that cannot fit."""
    tail = history.copy()
    starts = [i for i, message in enumerate(tail) if message.role == "user"]
    if memory and len(starts) > 2:
        full = [Message(role="system", content=system + memory), *tail, event]
        if estimate(full, tools) >= input_budget(settings) * 0.75:
            tail = tail[starts[-2] :]
    while True:
        prompt = system + memory
        messages = [Message(role="system", content=prompt), *tail, event]
        if estimate(messages, tools) <= input_budget(settings):
            return messages
        starts = [i for i, message in enumerate(tail) if message.role == "user"]
        if starts:
            tail = tail[starts[1] :] if len(starts) > 1 else []
        elif memory:
            memory = ""
        else:
            raise ChatError(
                "This turn exceeds model_context_tokens. "
                "Shorten it or check the host's context size."
            )
