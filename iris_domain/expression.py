"""The model suggests an expression. Only these labels reach the display."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

FaceExpression = Literal[
    "neutral",
    "curious",
    "amused",
    "concerned",
    "focused",
    "delighted",
    "surprised",
    "skeptical",
    "sleepy",
    "sad",
    "playful",
    "proud",
]
FACE_EXPRESSIONS: tuple[FaceExpression, ...] = get_args(FaceExpression)
FACE_HEADER = "X-Iris-Face"
_PREFIX = "[[expression:"
FACE_PROMPT = (
    "\nFor the final spoken reply, put one face cue on the first line, for example "
    "[[expression:curious]]. Use only " + ", ".join(FACE_EXPRESSIONS) + ". "
    "Choose the expression that suits the tone of your reply, or neutral. "
    "After that line, write the words Iris should say aloud. "
    "Double brackets are only for face cues. To use a tool, use the API's function-call "
    "interface. Keep face cues out of tool calls and their arguments."
)


@dataclass(frozen=True)
class ModelReply:
    text: str
    expression: FaceExpression | None = None


def read_model_reply(text: str, *, face: bool) -> ModelReply:
    if not face:
        return ModelReply(text)
    leading = text.lstrip()
    if not leading.lower().startswith(_PREFIX):
        return ModelReply(text, "neutral")
    first_line, newline, rest = leading.partition("\n")
    label, closed, inline = first_line[len(_PREFIX) :].partition("]]")
    label = label.strip().lower()
    expression = label if closed and label in FACE_EXPRESSIONS else "neutral"
    # Also discard an unfinished cue line when the model puts its reply below it.
    body = (inline + newline + rest if closed else rest).lstrip()
    return ModelReply(body, expression)
