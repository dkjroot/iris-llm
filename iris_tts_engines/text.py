"""Prepare the spoken copy of a reply for the synthesizer."""

from __future__ import annotations

import re
from unicodedata import category

import regex  # type: ignore[import-untyped]

_EMPHASIS = (
    re.compile(r"(?<![\w*\\])(\*{1,3})(?=\S)(.*?\S)\1(?![\w*])", re.DOTALL),
    re.compile(r"(?<![\w_\\])(_{1,3})(?=\S)(.*?\S)\1(?![\w_])", re.DOTALL),
)

# Match entire graphemes so flags, skin tones, joiners and keycaps leave no
# fragments behind. Keep text forms of common marks such as © and ™.
_PICTOGRAPH = (
    r"(?=[#*0-9]\uFE0F?\u20E3|\p{Emoji}\uFE0F|"
    r"\p{Emoji_Presentation}|"
    r"(?![©®™])(?=\p{So})\p{Extended_Pictographic})\X"
)
_EMOJI = regex.compile(rf"(?<![\p{{Zs}}\t])[\p{{Zs}}\t]*(?:{_PICTOGRAPH}[\p{{Zs}}\t]*)+")


def _emoji_gap(match: regex.Match[str]) -> str:
    text = match.string
    before = text[match.start() - 1] if match.start() else "\n"
    after = text[match.end()] if match.end() < len(text) else "\n"
    if (
        before.isspace()
        or after.isspace()
        or category(before) in {"Ps", "Pi"}
        or category(after) in {"Pe", "Pf"}
        or after in ",.!?;:%…，。！？；：、"
    ):
        return ""
    # An emoji can be the only boundary between two words.
    return " "


def plain_speech_text(text: str) -> str:
    # Keep code spans literal: operators such as ** can have a real meaning.
    pieces = re.split(r"(`+.*?`+)", text, flags=re.DOTALL)
    for index in range(0, len(pieces), 2):
        previous = None
        while previous != pieces[index]:
            previous = pieces[index]
            for pattern in _EMPHASIS:
                pieces[index] = pattern.sub(r"\2", pieces[index])
    spoken: str
    spoken, _ = _EMOJI.subn(_emoji_gap, "".join(pieces))
    if not any(character.isalnum() for character in spoken):
        return ""
    return spoken


_ABBREVIATIONS = frozenset(
    {
        "mr",
        "mrs",
        "ms",
        "dr",
        "prof",
        "sr",
        "jr",
        "st",
        "vs",
        "etc",
        "eg",
        "ie",
        "e.g",
        "i.e",
    }
)
_SENTENCE_END = re.compile(r'([.!?…]["’”)\]]*(?:\s+|$)|\n+)')


def _ends_with_abbreviation(prefix: str) -> bool:
    trimmed = prefix.rstrip(' "’”)]')
    if not trimmed.endswith("."):
        return False
    token = trimmed[:-1].rsplit(None, 1)[-1].lower()
    if token in _ABBREVIATIONS:
        return True
    # Single-letter initials (e.g. "J. K. Rowling") or decimal numbers ("3.14").
    return len(token) == 1 and token.isalpha()


_STREAM_BOUNDARY = re.compile(r'([.!?…]["’”)\]]*(?:\s+|\n+)|\n+)')


def pop_streaming_sentences(buffer: str, *, min_chars: int = 12) -> tuple[list[str], str]:
    """Extract completed sentences from an in-flight LLM buffer without touching the live tail."""
    if not buffer:
        return [], ""
    ready: list[str] = []
    start = 0
    for match in _STREAM_BOUNDARY.finditer(buffer):
        end = match.end()
        candidate = buffer[start:end].strip()
        if not candidate:
            start = end
            continue
        if _ends_with_abbreviation(candidate):
            continue
        if len(candidate) < min_chars and not ready:
            continue
        ready.append(candidate)
        start = end
    return ready, buffer[start:]


def split_speech_sentences(text: str, *, min_chars: int = 24) -> list[str]:
    """Split spoken text into natural sentence chunks for low-latency TTS streaming."""
    cleaned = text.strip()
    if not cleaned:
        return []
    parts: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(cleaned):
        end = match.end()
        candidate = cleaned[start:end].strip()
        if not candidate:
            continue
        # Avoid splitting inside decimal numbers like "3.5" or abbreviations like "Dr. Smith".
        if match.group(0).startswith(".") and end < len(cleaned) and cleaned[end].isdigit():
            continue
        if _ends_with_abbreviation(candidate):
            continue
        parts.append(candidate)
        start = end
    tail = cleaned[start:].strip()
    if tail:
        parts.append(tail)
    if not parts:
        return [cleaned]
    # Coalesce very short adjacent fragments (except keep sentence 1 responsive if >= 12 chars).
    merged: list[str] = []
    for part in parts:
        if merged and len(merged[-1]) < (12 if len(merged) == 1 else min_chars):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    if len(merged) > 1 and len(merged[-1]) < 12:
        merged[-2] = f"{merged[-2]} {merged.pop()}"
    return merged
