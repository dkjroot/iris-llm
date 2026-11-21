"""Conservative prompt sizing until adapters expose their model's tokenizer."""

from __future__ import annotations

import json

from iris_chat_engines.messages import ChatError, Message
from iris_settings import Settings


def estimate_bytes(size: int) -> int:
    # Two UTF-8 bytes per token conservatively covers dense JSON and non-ASCII
    # prose without starving default 8k context windows when tools are enabled.
    return (size + 1) // 2


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def estimate(messages: list[Message], tools: list[dict[str, object]]) -> int:
    payload = {"messages": [m.model_dump(exclude_none=True) for m in messages], "tools": tools}
    return estimate_bytes(_json_bytes(payload)) + 32 * len(messages)


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
    budget = input_budget(settings)
    tools_bytes = _json_bytes(tools)
    event_bytes = _json_bytes(event.model_dump(exclude_none=True))
    tail = history.copy()
    tail_bytes = [_json_bytes(m.model_dump(exclude_none=True)) for m in tail]

    def total_estimate(prompt_text: str, active_bytes: list[int]) -> int:
        count = len(active_bytes) + 2
        sys_bytes = _json_bytes({"role": "system", "content": prompt_text})
        payload_bytes = (
            27 + sys_bytes + sum(active_bytes) + event_bytes + 2 * (count - 1) + tools_bytes
        )
        return estimate_bytes(payload_bytes) + 32 * count

    starts = [i for i, message in enumerate(tail) if message.role == "user"]
    if memory and len(starts) > 2:
        if total_estimate(system + memory, tail_bytes) >= budget * 0.75:
            cut = starts[-2]
            tail = tail[cut:]
            tail_bytes = tail_bytes[cut:]
    while True:
        prompt = system + memory
        if total_estimate(prompt, tail_bytes) <= budget:
            return [Message(role="system", content=prompt), *tail, event]
        starts = [i for i, message in enumerate(tail) if message.role == "user"]
        if starts:
            if len(starts) > 1:
                cut = starts[1]
                tail = tail[cut:]
                tail_bytes = tail_bytes[cut:]
            else:
                tail = []
                tail_bytes = []
        elif memory:
            memory = ""
        else:
            raise ChatError(
                "This turn exceeds model_context_tokens. "
                "Shorten it or check the host's context size."
            )
