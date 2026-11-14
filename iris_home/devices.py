"""Device capabilities and the direct Matter adapter."""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Protocol

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


@dataclass(frozen=True)
class Device:
    switch: Switch
    meters: dict[Metric, Meter]


class MatterDeviceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    node: int = Field(ge=1, le=0xFFFFFFEFFFFFFFFF)
    switch_endpoint: int = Field(default=1, ge=1, le=65534)
    power_endpoint: int | None = Field(default=None, ge=1, le=65534)
    energy_endpoint: int | None = Field(default=None, ge=1, le=65534)


class ChipTool:
    """Serialise access to the local controller's fabric files."""

    def __init__(self, executable: str, storage: Path) -> None:
        self.executable = executable
        self.storage = storage
        self.lock = Lock()

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
        result = self.controller.run(
            "onoff",
            "read",
            "on-off",
            node=self.config.node,
            endpoint=self.config.switch_endpoint,
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
        result = self.controller.run(
            cluster,
            "read",
            attribute,
            node=self.config.node,
            endpoint=endpoint,
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
