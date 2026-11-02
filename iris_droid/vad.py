"""Local Silero speech detection and streaming turn capture, without PyTorch."""

from __future__ import annotations

import hashlib
import math
from collections import deque
from collections.abc import Callable
from pathlib import Path
from time import monotonic
from typing import Any, Protocol

import httpx
import numpy as np
import speech_recognition as sr

MODEL_REVISION = "be95df9152c0d7618fa1edfeb296fc3dae32376f"  # Silero v6.2
MODEL_URL = (
    f"https://raw.githubusercontent.com/snakers4/silero-vad/{MODEL_REVISION}"
    "/src/silero_vad/data/silero_vad.onnx"
)
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
RATE = 16000
FRAME_SAMPLES = 512
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_SECONDS = FRAME_SAMPLES / RATE


def download_model(path: Path) -> None:
    """Explicit preparation only: verify the pinned download before replacing a file."""
    response = httpx.get(MODEL_URL, follow_redirects=True, timeout=60)
    response.raise_for_status()
    data = response.content
    if hashlib.sha256(data).hexdigest() != MODEL_SHA256:
        raise ValueError("Silero model checksum mismatch; existing model left unchanged")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class SpeechDetector(Protocol):
    def reset(self) -> None: ...

    def probability(self, pcm: bytes) -> float: ...


class SileroVAD:
    """16 kHz, single-stream adapter for the pinned Silero ONNX model."""

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise ValueError(
                f"VAD model not found: {path}. Prepare it with "
                "python iris.py client --download-vad-model PATH"
            )
        if hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("VAD model is not the supported Silero v6.2 file; download it again")
        try:
            import onnxruntime as ort
        except ImportError:
            raise ImportError('Speech detection needs: python -m pip install -e ".[vad]"') from None
        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def probability(self, pcm: bytes) -> float:
        if len(pcm) != FRAME_BYTES:
            raise ValueError("Silero expects 512 mono int16 samples per frame")
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)[None, :] / 32768.0
        audio = np.concatenate((self.context, samples), axis=1)
        output, self.state = self.session.run(
            None, {"input": audio, "state": self.state, "sr": np.array(RATE, dtype=np.int64)}
        )
        self.context = samples[:, -64:].copy()
        probability = float(output[0][0])
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError("VAD returned an invalid speech probability")
        return probability


def listen_for_speech(
    source: Any,
    detector: SpeechDetector,
    *,
    timeout: float,
    limit: float,
    pause: float,
    threshold: float = 0.5,
    clock: Callable[[], float] = monotonic,
) -> bytes:
    """Return bounded 16 kHz PCM; rejected noises don't restart the waiting timeout.

    Keep native device reads and their overflow checks, but feed the detector
    32 ms windows through a stateful rate converter. Three consecutive positive
    windows confirm speech; 320 ms of pre-roll preserves its opening sounds.
    """
    if not 0 < threshold < 1:
        raise ValueError("VAD threshold must be between 0 and 1, exclusive")
    detector.reset()
    deadline = clock() + timeout
    converter_state = None
    pending = b""
    history: deque[bytes] = deque(maxlen=10)
    frames: list[bytes] = []
    consecutive = 0
    silence = 0
    elapsed = 0.0
    max_samples = int(limit * RATE)
    silence_frames = math.ceil(pause / FRAME_SECONDS)
    while True:
        if not frames and clock() >= deadline:
            raise sr.WaitTimeoutError("No speech detected before the listening deadline")
        raw = source.stream.read(source.CHUNK)
        if not raw:
            raise OSError("Microphone stopped during speech detection")
        converted, converter_state = sr.audioop.ratecv(
            raw, 2, 1, source.SAMPLE_RATE, RATE, converter_state
        )
        pending += converted
        while len(pending) >= FRAME_BYTES:
            frame, pending = pending[:FRAME_BYTES], pending[FRAME_BYTES:]
            elapsed += FRAME_SECONDS
            if not frames and (elapsed > timeout or clock() >= deadline):
                raise sr.WaitTimeoutError("No speech detected before the listening deadline")
            probability = detector.probability(frame)
            if not frames:
                history.append(frame)
                consecutive = consecutive + 1 if probability >= threshold else 0
                if consecutive < 3:
                    continue
                frames = list(history)
            else:
                frames.append(frame)
                # Hysteresis avoids fragmenting a quiet word around the start threshold.
                silence = silence + 1 if probability < threshold * 0.7 else 0
            if silence >= silence_frames or len(frames) * FRAME_SAMPLES >= max_samples:
                return b"".join(frames)[: max_samples * 2]
