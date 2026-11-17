"""Small deterministic adapters shared by tests and the offline benchmark.

They never open audio devices or make network requests. ScriptedBackend rejects
unplanned calls unless a test explicitly provides a fallback response.
"""

from collections import deque
from collections.abc import Generator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import numpy as np

from iris_chat_engines.messages import Message
from iris_droid.server import Brain
from iris_home.devices import DeviceError
from iris_settings import Settings


@dataclass
class FakeClock:
    instant: datetime = field(default_factory=lambda: datetime(2026, 11, 17, tzinfo=UTC))
    elapsed: float = 0.0

    def now(self) -> datetime:
        return self.instant

    def monotonic(self) -> float:
        return self.elapsed

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Use set_wall_time for wall-clock corrections")
        self.elapsed += seconds
        self.instant += timedelta(seconds=seconds)

    def set_wall_time(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("Use an aware datetime")
        self.instant = instant


class ScriptedBackend:
    def __init__(self, replies=(), *, fallback: Message | None = None):
        self.replies = deque(replies)
        self.fallback = fallback
        self.calls: list[tuple[list[Message], list[dict[str, object]]]] = []

    def complete(self, messages, tools):
        self.calls.append(([m.model_copy(deep=True) for m in messages], tools.copy()))
        if not self.replies and self.fallback is None:
            raise AssertionError("Unplanned model call")
        reply = self.replies.popleft() if self.replies else self.fallback
        if isinstance(reply, Exception):
            raise reply
        return reply.model_copy(deep=True)

    def handle(self, request: httpx.Request) -> httpx.Response:
        import json

        if request.url.host != "model.invalid":
            raise AssertionError(f"Unexpected outbound service: {request.url.host}")
        payload = json.loads(request.content)
        reply = self.complete(
            [Message.model_validate(m) for m in payload["messages"]], payload.get("tools", [])
        )
        return httpx.Response(200, json={"choices": [{"message": reply.model_dump()}]})


@dataclass
class FakeSTT:
    text: str = "Hello Iris"
    calls: int = 0

    def recognize(self, audio):
        self.calls += 1
        return self.text

    def is_offline(self) -> bool:
        return True


@dataclass
class FakeTTS:
    sample_rate: int = 24000
    calls: list[str] = field(default_factory=list)
    failure: Exception | None = None

    def synthesize(self, text):
        self.calls.append(text)
        if self.failure is not None:
            raise self.failure
        return np.full(2400, 0.1, dtype=np.float32), self.sample_rate

    def stream(self, text) -> Generator[bytes]:
        samples, _ = self.synthesize(text)
        pcm = (samples * 32767).astype("<i2").tobytes()
        yield pcm[:2400]
        yield pcm[2400:]


@dataclass
class FakeSwitch:
    commands: list[str] = field(default_factory=list)
    offline: bool = False

    def state(self):
        return self.commands[-1] if self.commands else "off"

    def set_state(self, state):
        if self.offline:
            raise DeviceError("Synthetic device offline")
        self.commands.append(state)


@dataclass
class BrainHarness:
    brain: Brain
    model: ScriptedBackend
    stt: FakeSTT
    tts: FakeTTS
    transport: httpx.Client


def make_harness(settings: Settings, model: ScriptedBackend) -> BrainHarness:
    transport = httpx.Client(transport=httpx.MockTransport(model.handle), trust_env=False)
    stt, tts = FakeSTT(), FakeTTS()
    try:
        return BrainHarness(Brain(settings, transport, stt, tts), model, stt, tts, transport)
    except BaseException:
        transport.close()
        raise


class KnownReviewDefect(AssertionError):
    """Only this assertion is eligible for an expected review failure.

    Setup errors and unrelated assertions must still fail the test normally.
    """


def require_resolution(condition: bool, message: str) -> None:
    if not condition:
        raise KnownReviewDefect(message)
