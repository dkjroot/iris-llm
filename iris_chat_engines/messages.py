"""Shared conversation messages exchanged with model backend adapters."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatError(RuntimeError):
    """A failed turn which leaves the conversation history alone."""


class FunctionCall(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str = Field(min_length=1, max_length=100)
    arguments: str = Field(max_length=8000)


class ToolCall(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str = Field(min_length=1, max_length=200)
    type: Literal["function"]
    function: FunctionCall


class Message(BaseModel):
    model_config = ConfigDict(strict=True)
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = Field(default=None, max_length=8)
    tool_call_id: str | None = None
    reasoning_content: str | None = None
