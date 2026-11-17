"""Incremental UTF-8 NDJSON framing with bounds checked before accumulation."""

from collections.abc import Iterable, Iterator


def ndjson_lines(
    chunks: Iterable[bytes],
    *,
    max_frame: int,
    max_total: int | None = None,
    max_frames: int | None = None,
) -> Iterator[str]:
    pending = bytearray()
    total = frames = 0
    for chunk in chunks:
        total += len(chunk)
        if max_total is not None and total > max_total:
            raise ValueError("Voice stream exceeds its total size limit")
        offset = 0
        while offset < len(chunk):
            newline = chunk.find(b"\n", offset)
            end = len(chunk) if newline < 0 else newline
            if len(pending) + end - offset > max_frame:
                raise ValueError("Oversized voice stream frame")
            pending.extend(memoryview(chunk)[offset:end])
            if newline < 0:
                break
            frames += 1
            if max_frames is not None and frames > max_frames:
                raise ValueError("Too many voice stream frames")
            yield pending.decode("utf-8").removesuffix("\r")
            pending.clear()
            offset = newline + 1
    if pending:
        raise ValueError("Truncated voice stream frame")
