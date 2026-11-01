"""Render emphasis as words for speech, leaving the displayed reply unchanged."""

from __future__ import annotations

import re

_EMPHASIS = (
    re.compile(r"(?<![\w*\\])(\*{1,3})(?=\S)(.*?\S)\1(?![\w*])", re.DOTALL),
    re.compile(r"(?<![\w_\\])(_{1,3})(?=\S)(.*?\S)\1(?![\w_])", re.DOTALL),
)


def plain_speech_text(text: str) -> str:
    # Keep code spans literal: operators such as ** can have a real meaning.
    pieces = re.split(r"(`+.*?`+)", text, flags=re.DOTALL)
    for index in range(0, len(pieces), 2):
        previous = None
        while previous != pieces[index]:
            previous = pieces[index]
            for pattern in _EMPHASIS:
                pieces[index] = pattern.sub(r"\2", pieces[index])
    return "".join(pieces)
