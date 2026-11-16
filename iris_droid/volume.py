"""Client-side speaker volume contracts, voice commands and mixer backends."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, RootModel, StrictInt, model_validator

Percent = Annotated[int, Field(strict=True, ge=0, le=100)]
_LOGGER = logging.getLogger(__name__)


class _SavedLevels(RootModel[list[StrictInt]]):
    root: list[StrictInt] = Field(min_length=1, max_length=32)


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
    def __init__(
        self, card: str | None = None, control: str = "Master", *, state_path: Path | None = None
    ) -> None:
        if not control.strip() or (card is not None and not card.strip()):
            raise ValueError("ALSA card and mixer control must not be blank")
        self.card = card
        self.control = control
        self.state_path = state_path

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

    @staticmethod
    def _raw_levels(output: str) -> list[int]:
        return [int(level) for level in re.findall(r": Playback (-?\d+) \[\d+%\]", output)]

    def restore(self) -> None:
        path = self.state_path
        if path is None:
            return
        try:
            levels = _SavedLevels.model_validate_json(path.read_bytes()).root
            current = self._run("sget", self.control)
            limits = re.search(r"^\s*Limits: Playback (-?\d+) - (-?\d+)\b", current, re.M)
            if limits is None or len(levels) != len(self._raw_levels(current)):
                raise ValueError("Saved levels do not match the playback control")
            low, high = map(int, limits.groups())
            if any(not low <= level <= high for level in levels):
                raise ValueError("Saved levels are outside the playback control's range")
            # Bare integers are raw levels in amixer, even with -M. This avoids
            # rounding a second time when restoring the displayed percentages.
            self._run("sset", self.control, "playback", ",".join(map(str, levels)))
            if self._raw_levels(self._run("sget", self.control)) != levels:
                raise OSError("Mixer read-back differs from the saved levels")
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as error:
            _LOGGER.warning("Could not restore speaker volume from %s: %s", path, error)

    def _remember(self, output: str) -> None:
        path = self.state_path
        if path is None:
            return
        temporary = None
        try:
            levels = _SavedLevels(self._raw_levels(output))
            path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(levels.model_dump_json())
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        except (OSError, ValueError) as error:
            _LOGGER.warning("Could not save speaker volume to %s: %s", path, error)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as error:
                    _LOGGER.warning(
                        "Could not remove temporary volume file %s: %s", temporary, error
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
        output = self._run("sget", self.control)
        state = self._state(output)
        if command.action in {"set", "change"}:
            self._remember(output)
        return state


def make_volume_backend(
    name: str, *, card: str | None = None, control: str = "Master", remember: bool = False
) -> VolumeBackend | None:
    if name == "none":
        return None
    if name == "alsa":
        state_path = None
        if remember:
            state_home = Path(os.environ.get("XDG_STATE_HOME", ""))
            if not state_home.is_absolute():
                state_home = Path.home() / ".local" / "state"
            mixer = sha256(json.dumps([card, control]).encode()).hexdigest()
            state_path = state_home / "iris" / "volume" / f"{mixer}.json"
        backend = AlsaVolume(card, control, state_path=state_path)
        backend.restore()
        return backend
    raise ValueError(f"Unknown volume backend: {name}")


def execute_volume(backend: VolumeBackend | None, command: VolumeCommand) -> VolumeResult:
    if backend is None:
        return VolumeResult(error="disabled")
    try:
        return VolumeResult(state=backend.execute(command))
    except OSError:
        return VolumeResult(error="unavailable")
