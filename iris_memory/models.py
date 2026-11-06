"""Source-linked, rebuildable derived memory, distinct from confirmed user profiles."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["topic", "user_assertion", "assistant_proposal", "decision", "open_question"]
    text: str = Field(min_length=1, max_length=500)
    sources: list[str] = Field(min_length=1, max_length=6)
    scope: Literal["real", "fictional", "hypothetical", "uncertain"]
    status: Literal["tentative", "confirmed", "closed"]
    follow_up_safe: bool = False
    supersedes: list[str] = Field(default_factory=list, max_length=6)


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    notes: list[Note] = Field(max_length=12)
