"""Typed rows and read snapshots at archive/service boundaries.

JSON-shaped records remain dictionaries for compatible exports and model payloads.
The row conversion is local to SQL; consumers no longer depend on unconstrained Any.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NamedTuple, NotRequired, TypedDict


@dataclass(frozen=True)
class Exchange:
    id: str
    origin: Literal["human", "automatic"]
    user_text: str
    assistant_text: str
    tool_names: tuple[str, ...] = ()


class PolicySnapshot(NamedTuple):
    private: bool
    epoch: int


class ForgetTarget(NamedTuple):
    conversation: str
    revision: str | None
    exchange_count: int


@dataclass(frozen=True)
class SessionSnapshot:
    policy: PolicySnapshot
    conversation: str | None
    revision: str | None
    exchanges: tuple[Exchange, ...] | None


class Source(TypedDict):
    sequence: int
    id: str
    created_at: str
    origin: Literal["human", "automatic"]
    user_text: str
    assistant_text: str
    excerpted: NotRequired[bool]


class SummaryJob(TypedDict):
    conversation: str
    owner: str
    attempts: int
    retry_at: float
    cursor: int
    pending_count: int
    last_served: NotRequired[int]


class NoteRecord(TypedDict):
    id: str
    summary: str
    owner: str
    conversation: str
    text: str
    kind: str
    scope: str
    status: str
    sources: str
    through_sequence: int
    created_at: str


class SourceExcerpt(TypedDict):
    exchange: str
    conversation: str
    created_at: str
    text: str


class SearchHit(SourceExcerpt):
    id: int
    owner: str
    sequence: int
    superseded: NotRequired[int]
    user_text: NotRequired[str]
    semantic_similarity: NotRequired[float]


class RecallSource(TypedDict):
    id: int | str
    exchange: str
    owner: str
    conversation: str
    sequence: int
    created_at: str
    user_text: str
    text: NotRequired[str]
    topic: NotRequired[str]
    semantic_similarity: NotRequired[float]
    semantic_only: NotRequired[bool]
    hierarchy_activity: NotRequired[float]


@dataclass(frozen=True)
class SummarySnapshot:
    policy: PolicySnapshot
    revision: str | None
    job: SummaryJob | None
    sources: tuple[Source, ...]
    notes: tuple[NoteRecord, ...]


class FactRecord(TypedDict):
    fact_key: str
    value: str
    scope: str
    conversation: str
    id: NotRequired[str]
    owner: NotRequired[str]
    kind: NotRequired[str]
    lifetime: NotRequired[str]
    importance: NotRequired[str]
    status: NotRequired[str]
    source: NotRequired[str]
    evidence: NotRequired[str]
    supersedes: NotRequired[str | None]
    detail_label: NotRequired[str | None]
    status_before_supersession: NotRequired[str | None]
    created_at: NotRequired[str]
    sequence: NotRequired[int]
    selection_reason: NotRequired[str]
    selected: NotRequired[bool]


class TopicRecord(TypedDict):
    id: str
    owner: str
    label: str
    scope: str
    source: str
    conversation: str
    created_at: str
    last_engaged_at: str | None
    engaged_conversation: str | None
    status: str
