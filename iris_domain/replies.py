"""Reply values shared by application and transport adapters."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from iris_domain.expression import FaceExpression
from iris_domain.volume import VolumeCommand


class TurnCancelled(Exception):
    """The provisional voice turn was discarded."""


class VoiceTiming(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    engine: str
    words: int = Field(ge=0)
    started_ns: int = Field(gt=0)
    synthesis_ns: int = Field(ge=0)
    audio_seconds: float = Field(ge=0, allow_inf_nan=False)


class TurnCompletion(BaseModel):
    """Authoritative server outcome, not proof that the client played the audio."""

    model_config = ConfigDict(extra="forbid", strict=True)
    outcome: Literal["committed"]
    private: bool
    exchange_id: str | None = Field(default=None, min_length=1, max_length=128)
    deleted_exchange_id: str | None = Field(default=None, min_length=1, max_length=128)


class TurnReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    heard: str
    text: str
    audio: str = ""  # Base64-encoded mono PCM WAV, at the synthesizer's sample rate.
    finished: bool = False
    companion_mode: bool = False
    volume: VolumeCommand | None = None
    expression: FaceExpression | None = None
    selected_personality: str | None = None
    _voice_timing: VoiceTiming | None = PrivateAttr(default=None)
    _streamed: bool = PrivateAttr(default=False)
    _exchange_id: str | None = PrivateAttr(default=None)
    _deleted_exchange_id: str | None = PrivateAttr(default=None)
    _completion: TurnCompletion | None = PrivateAttr(default=None)


class VoiceStreamStart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event: Literal["start"]
    reply: TurnReply
    rate: int = Field(ge=8000, le=48000)
    started_ns: int = Field(gt=0)
    first_audio_ns: int = Field(ge=0)
