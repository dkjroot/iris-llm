"""Small, disposable speech envelopes, timed to the speaker's output clock."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from threading import Lock
from time import monotonic
from typing import Any

import numpy as np
import numpy.typing as npt

ENVELOPE_STEP = 0.02


def mouth_level(rms: float) -> float:
    # A noise gate keeps silence closed; compression preserves quieter syllables.
    return min(1.0, math.sqrt(max(0.0, rms - 0.008) / 0.18))


class SpeechEnvelope:
    """Keep at most 2.56 seconds of levels, never recordings or transcripts."""

    def __init__(self, *, on_change: Callable[[], None] | None = None) -> None:
        self._points: deque[tuple[float, float]] = deque(maxlen=128)
        self._lock = Lock()
        self._on_change = on_change

    def clear(self) -> None:
        with self._lock:
            changed = bool(self._points)
            self._points.clear()
        if changed and self._on_change is not None:
            self._on_change()

    def feed(self, samples: npt.NDArray[np.int16], rate: int, delay: float) -> None:
        start = monotonic() + max(0.0, min(2.0, delay))
        step = max(1, round(rate * ENVELOPE_STEP))
        normalized = samples.astype(np.float32) * (1.0 / 32768.0)
        points = []
        for offset in range(0, len(normalized), step):
            chunk = normalized[offset : offset + step]
            level = mouth_level(float(np.sqrt(np.mean(chunk * chunk))))
            points.append((start + offset / rate, round(level, 3)))
        # An explicit end closes the mouth if playback stalls or the device stops.
        points.append((start + len(normalized) / rate, 0.0))
        with self._lock:
            # Replace the previous block's closing point when the next begins.
            while self._points and self._points[-1][0] >= start:
                self._points.pop()
            self._points.extend(points)
        if self._on_change is not None:
            self._on_change()

    def snapshot(self, now: float) -> list[list[float]]:
        with self._lock:
            while len(self._points) > 1 and self._points[1][0] <= now:
                self._points.popleft()
            return [[round(at - now, 4), level] for at, level in self._points]


def read_mouth(value: Any, now: float) -> tuple[tuple[float, float], ...]:
    """Validate the optional local-display envelope without trusting JSON values."""
    if not isinstance(value, list) or len(value) > 128:
        return ()
    points: list[tuple[float, float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            return ()
        offset, level = point
        if not all(isinstance(v, (float, int)) and math.isfinite(v) for v in point):
            return ()
        if not -3 <= offset <= 3 or not 0 <= level <= 1:
            return ()
        at = now + offset
        if points and at < points[-1][0]:
            return ()
        points.append((at, level))
    return tuple(points)


def sample_mouth(points: tuple[tuple[float, float], ...], now: float) -> float:
    level = 0.0
    for at, value in points:
        if at > now:
            break
        level = value if now - at < 0.15 else 0.0
    return level
