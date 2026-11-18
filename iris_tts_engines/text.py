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
    removed: int
    spoken, removed = _EMOJI.subn(_emoji_gap, "".join(pieces))
    if removed and not any(character.isalnum() for character in spoken):
        return ""
    return spoken
