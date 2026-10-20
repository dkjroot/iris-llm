"""Capture a manual turn without using silence to guess when it is finished."""

from collections import deque
from collections.abc import Callable
from math import ceil

from speech_recognition import AudioData, AudioSource


def record_while_held(
    source: AudioSource,
    start_requested: Callable[[], bool],
    is_held: Callable[[], bool],
    phrase_time_limit: float,
) -> AudioData:
    if source.stream is None:
        raise ValueError("Open the microphone before starting hold-to-talk")

    def read(frames: int) -> bytes:
        data = bytes(source.stream.read(frames))
        if not data:
            raise OSError("The microphone stopped returning audio")
        return data

    # A key held during the previous reply must be released before another turn.
    # Drain the microphone while waiting so stale buffered audio cannot pile up.
    while is_held():
        read(source.CHUNK)
    print("You: (ready: hold Space to speak, release to send...) ", flush=True)
    preroll: deque[bytes] = deque(maxlen=max(1, ceil(0.15 * source.SAMPLE_RATE / source.CHUNK)))
    while not start_requested():
        preroll.append(read(source.CHUNK))

    # Keep a tiny lead-in so the polling interval doesn't eat the first syllable.
    # Everything else heard while waiting has already been discarded.
    chunks = list(preroll)
    frames = 0
    limit = max(1, int(phrase_time_limit * source.SAMPLE_RATE))
    while is_held() and frames < limit:
        data = read(min(source.CHUNK, limit - frames))
        chunks.append(data)
        frames += len(data) // source.SAMPLE_WIDTH
    if frames >= limit:
        print("(recording time limit reached; release Space before the next turn)")
    else:
        print("(released Space)")
    return AudioData(b"".join(chunks), source.SAMPLE_RATE, source.SAMPLE_WIDTH)
