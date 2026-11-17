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
from typing import Protocol

from pydantic import Field, RootModel, StrictInt

from iris_domain.volume import (
    Percent as Percent,
)
from iris_domain.volume import (
    VolumeCommand as VolumeCommand,
)
from iris_domain.volume import (
    VolumeResult as VolumeResult,
)
from iris_domain.volume import (
    VolumeState as VolumeState,
)
from iris_domain.volume import (
    volume_command as volume_command,
)

_LOGGER = logging.getLogger(__name__)


class _SavedLevels(RootModel[list[StrictInt]]):
    root: list[StrictInt] = Field(min_length=1, max_length=32)


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
