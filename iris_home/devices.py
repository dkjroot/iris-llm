"""Device capabilities and the direct Matter adapter."""

from __future__ import annotations

import logging
import re
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from iris_home.protocol import Metric, PowerState

LOG = logging.getLogger(__name__)
ANSI_COLOUR = re.compile(r"\x1b\[[0-9;]*m")


class DeviceError(Exception):
    """An operation could not be completed or confirmed."""


class Switch(Protocol):
    def state(self) -> PowerState: ...
    def set_state(self, state: PowerState) -> None: ...


class Meter(Protocol):
    def read(self, metric: Metric) -> dict[str, object]: ...


class Sensor(Protocol):
    def read(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class Device:
    switch: Switch
    meters: dict[Metric, Meter]
    sensors: dict[str, Sensor] = field(default_factory=dict)


class MatterDeviceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    node: int = Field(ge=1, le=0xFFFFFFEFFFFFFFFF)
    switch_endpoint: int = Field(default=1, ge=1, le=65534)
    power_endpoint: int | None = Field(default=None, ge=1, le=65534)
    energy_endpoint: int | None = Field(default=None, ge=1, le=65534)


class ChipTool:
    """Serialise access to the local controller's fabric files.

    When interactive=True, reuses a persistent `chip-tool interactive start`
    process across calls (automatically falling back/restarting on failure).
    """

    def __init__(self, executable: str, storage: Path, *, interactive: bool = False) -> None:
        self.executable = executable
        self.storage = storage
        self.interactive = interactive
        self.lock = Lock()
        self._process: subprocess.Popen[str] | None = None
        self._lines: Queue[str | None] = Queue()

    def _close_interactive(self) -> None:
        proc = self._process
        self._process = None
        if proc is not None:
            if proc.stdin is not None:
                with suppress(OSError):
                    proc.stdin.close()
            if proc.poll() is None:
                with suppress(OSError):
                    proc.terminate()
                with suppress(OSError, subprocess.TimeoutExpired):
                    proc.wait(timeout=1)
                if proc.poll() is None:
                    with suppress(OSError):
                        proc.kill()
                    with suppress(OSError, subprocess.TimeoutExpired):
                        proc.wait(timeout=1)

    def close(self) -> None:
        with self.lock:
            self._close_interactive()

    def _ensure_interactive(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self._close_interactive()
        self._lines = Queue()
        proc = subprocess.Popen(
            [
                self.executable,
                "interactive",
                "start",
                "--storage-directory",
                str(self.storage),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            bufsize=1,
        )
        assert proc.stdout is not None
        lines = self._lines

        def _reader() -> None:
            try:
                for line in proc.stdout:  # type: ignore[union-attr]
                    lines.put(line)
            finally:
                lines.put(None)

        Thread(target=_reader, name="chip-tool-reader", daemon=True).start()
        self._process = proc
        return proc

    def _run_interactive(self, *command: str, node: int, endpoint: int) -> str:
        proc = self._ensure_interactive()
        assert proc.stdin is not None
        # Drain any stale output before sending the next command.
        while True:
            try:
                self._lines.get_nowait()
            except Empty:
                break
        line = " ".join([*command, str(node), str(endpoint), "--timeout", "6"]) + "\n"
        try:
            proc.stdin.write(line)
            proc.stdin.flush()
        except OSError as error:
            self._close_interactive()
            raise DeviceError("The Matter command failed or timed out.") from error
        collected: list[str] = []
        deadline = time.monotonic() + 8.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._close_interactive()
                raise DeviceError("The Matter command failed or timed out.")
            try:
                chunk = self._lines.get(timeout=remaining)
            except Empty:
                self._close_interactive()
                raise DeviceError("The Matter command failed or timed out.") from None
            if chunk is None:
                self._close_interactive()
                raise DeviceError("The Matter command failed or timed out.")
            cleaned = ANSI_COLOUR.sub("", chunk)
            collected.append(cleaned)
            if "CHIP:TOO: Run command failure:" in cleaned:
                raise DeviceError("The Matter command failed or timed out.")
            if (
                "CHIP:TOO: Command execution complete" in cleaned
                or "CHIP:DMG: Received Command Response Status" in cleaned
                or "CHIP:TOO:   " in cleaned
                and command[1:2] == ("read",)
            ):
                # Allow any trailing decoded lines to flush briefly.
                end_grace = time.monotonic() + 0.05
                while time.monotonic() < end_grace:
                    try:
                        extra = self._lines.get(timeout=max(0.005, end_grace - time.monotonic()))
                    except Empty:
                        break
                    if extra is None:
                        break
                    collected.append(ANSI_COLOUR.sub("", extra))
                return "".join(collected)

    def run(self, *command: str, node: int, endpoint: int) -> str:
        args = [
            self.executable,
            *command,
            str(node),
            str(endpoint),
            "--storage-directory",
            str(self.storage),
            "--timeout",
            "6",
        ]
        with self.lock:
            if self.interactive:
                try:
                    return self._run_interactive(*command, node=node, endpoint=endpoint)
                except (DeviceError, OSError):
                    LOG.warning("Interactive CHIP Tool session failed; falling back to one-shot.")
                    self._close_interactive()
            try:
                result = subprocess.run(
                    args,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    errors="replace",
                    timeout=8,
                    check=True,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                output = error.output or ""
                if isinstance(output, bytes):
                    output = output.decode(errors="replace")
                tail = ANSI_COLOUR.sub("", output).rstrip()[-4000:]
                LOG.warning("CHIP Tool failed: %s\n%s", error, tail or "No output captured.")
                raise DeviceError("The Matter command failed or timed out.") from error
            except (OSError, subprocess.SubprocessError) as error:
                LOG.warning("Could not run CHIP Tool with %r: %s", args, error)
                raise DeviceError("The Matter command failed or timed out.") from error
        return ANSI_COLOUR.sub("", result.stdout)


def _decoded_report(output: str) -> str:
    """Prefer decoded CHIP:TOO: lines so unrelated transport logs cannot spoof values."""
    too_lines = [line for line in output.splitlines() if "CHIP:TOO:" in line]
    return "\n".join(too_lines) if too_lines else output


# chip-tool prints attribute names followed by values in its decoded response.
# Matter represents these values in milliwatts, millivolts, milliamps and milliwatt-hours.
POWER_ATTRIBUTES: dict[Metric, tuple[str, str, str]] = {
    "power": ("active-power", "ActivePower", "W"),
    "voltage": ("voltage", "Voltage", "V"),
    "current": ("active-current", "ActiveCurrent", "A"),
}


class MatterPlug:
    def __init__(self, controller: ChipTool, config: MatterDeviceConfig) -> None:
        self.controller = controller
        self.config = config

    def state(self) -> PowerState:
        result = _decoded_report(
            self.controller.run(
                "onoff",
                "read",
                "on-off",
                node=self.config.node,
                endpoint=self.config.switch_endpoint,
            )
        )
        match = re.search(r"\bOnOff:\s*(TRUE|FALSE|true|false|0|1)\s*$", result, re.MULTILINE)
        if match is None:
            raise DeviceError("The plug did not report its switch state.")
        return "on" if match[1].lower() in {"true", "1"} else "off"

    def set_state(self, state: PowerState) -> None:
        self.controller.run(
            "onoff",
            state,
            node=self.config.node,
            endpoint=self.config.switch_endpoint,
        )

    def close(self) -> None:
        self.controller.close()

    def read(self, metric: Metric) -> dict[str, object]:
        if metric == "energy":
            endpoint = self.config.energy_endpoint
            cluster = "electricalenergymeasurement"
            attribute, label, unit = "cumulative-energy-imported", "Energy", "Wh"
        else:
            endpoint = self.config.power_endpoint
            cluster = "electricalpowermeasurement"
            attribute, label, unit = POWER_ATTRIBUTES[metric]
        if endpoint is None:
            raise DeviceError("This meter is not configured for that device.")
        result = _decoded_report(
            self.controller.run(
                cluster,
                "read",
                attribute,
                node=self.config.node,
                endpoint=endpoint,
            )
        )
        # Struct fields can appear on their own line or inline with the other fields.
        match = re.search(rf"\b{label}:\s*(-?[0-9]{{1,19}})(?=\s|[,}}]|$)", result)
        if match is None:
            raise DeviceError("The plug did not report an available meter reading.")
        return {"metric": metric, "value": int(match[1]) / 1000, "unit": unit}

    def device(self) -> Device:
        meters: dict[Metric, Meter] = {}
        if self.config.power_endpoint is not None:
            meters.update(dict.fromkeys(POWER_ATTRIBUTES, self))
        if self.config.energy_endpoint is not None:
            meters["energy"] = self
        return Device(self, meters)


class DeviceDriver(Protocol):
    def build_devices(
        self, service_config: Any, device_configs: dict[str, Any]
    ) -> dict[str, Device]: ...


class MatterDriver:
    def build_devices(
        self, service_config: Any, device_configs: dict[str, Any]
    ) -> dict[str, Device]:
        controller = ChipTool(
            service_config.chip_tool,
            service_config.matter_storage,
            interactive=service_config.chip_tool_interactive,
        )
        return {
            name: MatterPlug(controller, device).device() for name, device in device_configs.items()
        }


DRIVER_REGISTRY: dict[str, DeviceDriver] = {"matter": MatterDriver()}
