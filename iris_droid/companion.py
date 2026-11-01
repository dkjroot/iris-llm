"""Companion personality, conversation seeds and client-owned silence timing."""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from queue import Empty, Queue
from threading import Thread
from time import monotonic

COMPANION_PROMPT = """
Companion mode is on. Role-play Iris as a relaxed, witty robot companion hanging out
with a friend. Conversation itself is the activity: chat, swap thoughts, follow a
tangent, have an opinion, and enjoy a bit of affectionate silliness. Respond to what
they actually say. Don't default to service-desk phrases such as 'How can I help?',
'Would you like me to do that?' or 'Would you like to know more?'. A reply can simply
be an observation or a joke; don't finish every turn with a question. Ask occasional
natural questions, one at a time. Keep it short enough to say aloud. Match their
mood: humour should feel spontaneous, not like a stand-up routine, and a serious
moment deserves care. You can play with being a robot, but you don't yet have eyes
or a body: don't claim to see the room, detect an arrival, or have real experiences
away from this conversation. Make imaginary scenarios clearly playful. Still help
with practical requests when asked, without turning every chat into a task.
"""

ICE_BREAKERS = (
    "Ask how their day went, leaving room for either a small victory or a good moan.",
    "Wonder what they're up to, without sounding like a supervisor checking in.",
    "Ask what's been on their mind lately, in an easygoing way.",
    "Invite the most mildly ridiculous thing that happened to them today.",
    "Offer a tiny, absurd would-you-rather involving a household object.",
    "Start 'I was thinking...' with a fresh, silly philosophical puzzle about everyday life.",
    "Invent a terrible robot business idea and invite them to improve or demolish it.",
    "Ponder which household appliance would be the worst flatmate, and why.",
    "Pitch a comically useless superpower and wonder whether they'd take it.",
    "Imagine a future robot body with one gloriously unnecessary feature.",
    "Ask which ordinary thing deserves an unnecessarily dramatic theme tune.",
    "Offer a gentle, debatable food opinion, with no invented personal eating experience.",
    "Wonder what tiny everyday inconvenience they'd abolish by royal decree.",
    "Invent a suspiciously specific award for surviving an ordinary day.",
    "Suggest a ridiculous name for a spaceship, pub, or robot band.",
    "Wonder what a pigeon would complain about in a strongly worded letter.",
    "Ask about a song, story, or game that's stuck with them recently.",
    "Pick up a light thread from the recent conversation with a fresh angle, if there is one.",
    "Share a whimsical 'what if' about time travel with a very mundane destination.",
    "Ask what small thing has made them smile lately, without demanding positivity.",
)


class CompanionTimer:
    """No timer during replies; ignored openers double the next random interval."""

    def __init__(
        self,
        minimum: float = 90,
        maximum: float = 240,
        *,
        clock: Callable[[], float] = monotonic,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        if not (
            math.isfinite(minimum) and math.isfinite(maximum) and 5 <= minimum <= maximum <= 3600
        ):
            raise ValueError("Companion idle range must satisfy 5 <= min <= max <= 3600 seconds")
        self.minimum, self.maximum = minimum, maximum
        self.clock, self.uniform = clock, uniform
        self.enabled = False
        self.unanswered = 0
        self._deadline = 0.0

    def update(self, enabled: bool, *, proactive: bool = False) -> None:
        self.enabled = enabled
        self.unanswered = min(3, self.unanswered + 1) if proactive and enabled else 0
        self._deadline = (
            self.clock() + self.uniform(self.minimum, self.maximum) * 2**self.unanswered
            if enabled
            else 0.0
        )

    def remaining(self) -> float:
        return max(0.0, self._deadline - self.clock()) if self.enabled else 0.0


class ConsoleInput:
    """Keep a single pending input call across idle timeouts, including on Windows."""

    def __init__(self) -> None:
        self._pending = False
        self._results: Queue[str | BaseException] = Queue()

    def read(self, prompt: str, timeout: float | None = None) -> str | None:
        if timeout is None and not self._pending:
            return input(prompt)
        if not self._pending:
            self._pending = True

            def read_line() -> None:
                try:
                    self._results.put(input(prompt))
                except (EOFError, KeyboardInterrupt) as error:
                    self._results.put(error)

            Thread(target=read_line, daemon=True).start()
        try:
            result = self._results.get(timeout=timeout)
        except Empty:
            return None
        self._pending = False
        if isinstance(result, BaseException):
            raise result
        return result
