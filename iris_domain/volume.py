"""Speaker command/result values and exact phrases; no mixer or persistence."""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Percent = Annotated[int, Field(strict=True, ge=0, le=100)]


class VolumeCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["get", "set", "change", "mute", "unmute"]
    value: int | None = None

    @model_validator(mode="after")
    def valid_value(self) -> VolumeCommand:
        if self.action == "set":
            valid = self.value is not None and 0 <= self.value <= 100
        elif self.action == "change":
            valid = self.value is not None and 1 <= abs(self.value) <= 100
        else:
            valid = self.value is None
        if not valid:
            raise ValueError("Invalid volume command value")
        return self


class VolumeState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    levels: list[Percent] = Field(min_length=1, max_length=32)
    muted: bool | None = None

    def description(self) -> str:
        low, high = min(self.levels), max(self.levels)
        level = str(low) if low == high else f"{low} to {high}"
        suffix = " The speaker is muted." if self.muted else ""
        return f"Speaker volume is {level} percent.{suffix}"


class VolumeResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    state: VolumeState | None = None
    error: Literal["disabled", "unavailable"] | None = None

    @model_validator(mode="after")
    def one_result(self) -> VolumeResult:
        if (self.state is None) == (self.error is None):
            raise ValueError("Expected either a volume state or an error")
        return self

    def description(self) -> str:
        if self.state is not None:
            return self.state.description()
        if self.error == "disabled":
            return "Speaker volume control is disabled on this client."
        return (
            "I couldn't check or change the speaker volume. Please check the client's mixer setup."
        )


def volume_command(text: str) -> VolumeCommand | None:
    """Recognise whole utterances only; conversation about volume is ordinary chat."""
    phrase = " ".join(text.casefold().replace("’", "'").split()).strip(" .!?,")
    phrase = re.sub(r"^please | please$", "", phrase)
    fixed = {
        "volume up": ("change", 10),
        "turn it up": ("change", 10),
        "turn the volume up": ("change", 10),
        "turn up the volume": ("change", 10),
        "volume down": ("change", -10),
        "turn it down": ("change", -10),
        "turn the volume down": ("change", -10),
        "turn down the volume": ("change", -10),
        "mute": ("mute", None),
        "mute the speaker": ("mute", None),
        "mute speaker": ("mute", None),
        "mute volume": ("mute", None),
        "unmute": ("unmute", None),
        "unmute the speaker": ("unmute", None),
        "unmute speaker": ("unmute", None),
        "unmute volume": ("unmute", None),
        "what's the volume": ("get", None),
        "what is the volume": ("get", None),
        "what's the volume level": ("get", None),
        "volume status": ("get", None),
        "what is the speaker volume": ("get", None),
    }
    if phrase in fixed:
        action, value = fixed[phrase]
        return VolumeCommand.model_validate({"action": action, "value": value})
    # Speech recognisers may return either words or digits for percentages.
    words = [
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
    ]
    numbers = {word: n for n, word in enumerate(words)}
    for tens, word in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"], 2
    ):
        numbers[word] = tens * 10
        for unit in range(1, 10):
            numbers[f"{word} {words[unit]}"] = tens * 10 + unit
    numbers.update({"one hundred": 100, "a hundred": 100})
    match = re.fullmatch(
        r"(?:set (?:the )?(?:speaker )?volume to|volume) (.+?)(?:\s*%| percent)?", phrase
    )
    if match:
        raw = match[1].replace("-", " ") if not match[1].startswith("-") else match[1]
        value = int(raw) if raw.isascii() and raw.isdigit() else numbers.get(raw)
        if value is not None and 0 <= value <= 100:
            return VolumeCommand(action="set", value=value)
    match = re.fullmatch(
        r"(?:turn (?:the )?volume (up|down)|(?:increase|raise|decrease|lower) (?:the )?volume)"
        r" by (.+?)(?:\s*%| percent)?",
        phrase,
    )
    if match:
        raw = match[2].replace("-", " ")
        value = int(raw) if raw.isascii() and raw.isdigit() else numbers.get(raw)
        if value is not None and 1 <= value <= 100:
            down = match[1] == "down" or phrase.startswith(("decrease", "lower"))
            return VolumeCommand(action="change", value=-value if down else value)
    return None
