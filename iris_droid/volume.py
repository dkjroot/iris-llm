"""Client-side speaker volume contracts, voice commands and mixer backends."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Annotated, Literal, Protocol

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


class VolumeBackend(Protocol):
    """Return the actual mixer state, or raise OSError if the operation fails."""

    def execute(self, command: VolumeCommand) -> VolumeState: ...


class AlsaVolume:
    def __init__(self, card: str | None = None, control: str = "Master") -> None:
        if not control.strip() or (card is not None and not card.strip()):
            raise ValueError("ALSA card and mixer control must not be blank")
        self.card = card
        self.control = control

    def _run(self, *arguments: str) -> str:
        command = ["amixer", "-M"]
        if self.card is not None:
            command.extend(["-c", self.card])
        command.extend(["--", *arguments])
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
                env={**os.environ, "LC_ALL": "C"},
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise OSError("ALSA mixer unavailable; check alsa-utils, card and control") from error
        return result.stdout

    @staticmethod
    def _state(output: str) -> VolumeState:
        lines = [line for line in output.splitlines() if "Playback" in line]
        levels = [int(value) for line in lines for value in re.findall(r"\[(\d+)%\]", line)]
        switches = [value for line in lines for value in re.findall(r"\[(on|off)\]", line)]
        if not levels or any(not 0 <= level <= 100 for level in levels):
            raise OSError("The selected ALSA control has no readable playback volume")
        return VolumeState(
            levels=levels, muted=all(s == "off" for s in switches) if switches else None
        )

    def execute(self, command: VolumeCommand) -> VolumeState:
        # Preflight avoids changing a capture-only or unreadable control.
        current = self._state(self._run("sget", self.control))
        if command.action == "get":
            return current
        if command.action in {"mute", "unmute"}:
            if current.muted is None:
                raise OSError("The selected ALSA control has no playback mute switch")
            value: str = command.action
        elif command.action == "set":
            value = f"{command.value}%"
        else:
            assert command.value is not None
            value = f"{abs(command.value)}%{'+' if command.value > 0 else '-'}"
        # Playback only, preserving capture gain and mute state on volume changes.
        self._run("sset", self.control, "playback", value)
        return self._state(self._run("sget", self.control))


def make_volume_backend(
    name: str, *, card: str | None = None, control: str = "Master"
) -> VolumeBackend | None:
    if name == "none":
        return None
    if name == "alsa":
        return AlsaVolume(card, control)
    raise ValueError(f"Unknown volume backend: {name}")


def execute_volume(backend: VolumeBackend | None, command: VolumeCommand) -> VolumeResult:
    if backend is None:
        return VolumeResult(error="disabled")
    try:
        return VolumeResult(state=backend.execute(command))
    except OSError:
        return VolumeResult(error="unavailable")
