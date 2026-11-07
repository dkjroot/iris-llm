"""Run the standalone Iris home automation service."""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import json
import logging
import os
import signal
import socket
import sqlite3
import ssl
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import FrameType
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from iris_home.devices import ChipTool, Device, DeviceError, MatterDeviceConfig, MatterPlug
from iris_home.protocol import (
    OPERATIONS,
    DeviceName,
    DeviceRequest,
    MeterRequest,
    Request,
    ScheduleRequest,
    SwitchRequest,
)
from iris_home.schedule import ScheduleConflict, Scheduler

LOG = logging.getLogger(__name__)


class MatterConfig(MatterDeviceConfig):
    protocol: Literal["matter"] = "matter"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    listen: str = "127.0.0.1"
    port: int = Field(default=8766, ge=1, le=65535)
    tls_cert: Path | None = None
    tls_key: Path | None = None
    timezone: str = "Europe/London"
    database: Path
    clock_sync_file: Path | None = None
    chip_tool: str = "chip-tool"
    matter_storage: Path
    devices: dict[DeviceName, MatterConfig] = Field(min_length=1)

    @field_validator("listen")
    @classmethod
    def ipv4_address(cls, value: str) -> str:
        return str(ipaddress.IPv4Address(value))

    @model_validator(mode="after")
    def check_configuration(self) -> Config:
        if bool(self.tls_cert) != bool(self.tls_key):
            raise ValueError("Configure both tls_cert and tls_key.")
        if not ipaddress.IPv4Address(self.listen).is_loopback and not self.tls_cert:
            raise ValueError("A LAN listener requires a TLS certificate and key.")
        names = [name.strip().casefold() for name in self.devices]
        if not all(names) or len(names) != len(set(names)):
            raise ValueError("Use distinct, non-blank device names.")
        addresses = {(device.node, device.switch_endpoint) for device in self.devices.values()}
        if len(addresses) != len(self.devices):
            raise ValueError("Map each Matter switch endpoint to one name.")
        return self

    def make_devices(self) -> dict[str, Device]:
        controller = ChipTool(self.chip_tool, self.matter_storage)
        return {
            name: MatterPlug(controller, device).device() for name, device in self.devices.items()
        }


class HomeAPI:
    def __init__(self, devices: dict[str, Device], scheduler: Scheduler) -> None:
        self.devices = devices
        self.scheduler = scheduler
        self.names = {name.strip().casefold(): name for name in devices}

    def execute(self, request: Request, operation: str) -> dict[str, object]:
        if not isinstance(request, DeviceRequest):
            return {
                "devices": [
                    {"device": name, "meters": list(device.meters)}
                    for name, device in self.devices.items()
                ]
            }
        name = self.names.get(request.device.strip().casefold())
        if name is None:
            raise KeyError("Unknown device. List the configured home devices.")
        device = self.devices[name]
        if isinstance(request, SwitchRequest):
            self.scheduler.switch(name, request.state)
            return {"device": name, "accepted": True, "requested_state": request.state}
        if isinstance(request, MeterRequest):
            meter = device.meters.get(request.metric)
            if meter is None:
                raise DeviceError("That meter is not configured for this device.")
            return {"device": name, **meter.read(request.metric)}
        if isinstance(request, ScheduleRequest):
            return self.scheduler.save(name, request)
        if operation == "schedule":
            return self.scheduler.read(name)
        return {"device": name, "state": device.switch.state()}


class HomeServer(HTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        api: HomeAPI,
        token: str,
        tls: ssl.SSLContext | None,
    ) -> None:
        self.api = api
        self.token = f"Bearer {token}".encode("ascii")
        self.tls = tls
        super().__init__(address, HomeHandler)

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        conn, address = super().get_request()
        conn.settimeout(3)
        if self.tls is not None:
            try:
                conn = self.tls.wrap_socket(conn, server_side=True)
            except OSError:
                conn.close()
                raise
        return conn, address


class HomeHandler(BaseHTTPRequestHandler):
    server: HomeServer

    def log_message(self, *_: object) -> None:
        # Keep untrusted request paths and headers out of the journal.
        pass

    def _reply(self, status: int, result: dict[str, object]) -> None:
        body = json.dumps(result, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        auth = self.headers.get("Authorization", "").encode("latin-1")
        if not hmac.compare_digest(auth, self.server.token):
            self._reply(401, {"error": "Home service authentication failed."})
            return
        operation = self.path.removeprefix("/v1/")
        schema = OPERATIONS.get(operation) if self.path.startswith("/v1/") else None
        if schema is None:
            self._reply(404, {"error": "Unknown home operation."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid body size")
            request = schema.model_validate_json(self.rfile.read(size))
        except (ValueError, TimeoutError):
            self._reply(400, {"error": "Invalid home request."})
            return
        try:
            result = self.server.api.execute(request, operation)
        except KeyError as error:
            self._reply(404, {"error": error.args[0]})
        except ScheduleConflict as error:
            self._reply(409, {"error": str(error)})
        except DeviceError as error:
            self._reply(503, {"error": str(error)})
        except sqlite3.Error:
            LOG.exception("Home service schedule database error")
            self._reply(503, {"error": "The schedule database is unavailable."})
        else:
            self._reply(200, result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    token = os.environ.pop("IRIS_HOME_TOKEN", "")
    if len(token) < 32 or not token.isascii() or not token.isalnum():
        parser.error("Set IRIS_HOME_TOKEN to at least 32 ASCII letters or digits.")
    try:
        config = Config.model_validate_json(args.config.read_bytes())
        zone = ZoneInfo(config.timezone)
        tls = None
        if config.tls_cert is not None and config.tls_key is not None:
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls.minimum_version = ssl.TLSVersion.TLSv1_2
            tls.load_cert_chain(config.tls_cert, config.tls_key)
        if not config.matter_storage.is_dir():
            raise ValueError("Create and commission the configured Matter storage directory first.")
        devices = config.make_devices()
    except (OSError, ValidationError, ValueError, ZoneInfoNotFoundError) as error:
        LOG.error("Could not configure home service: %s", error)
        return 1

    def stop(_signal: int, _frame: FrameType | None) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    ready = config.clock_sync_file
    try:
        with (
            Scheduler(
                config.database,
                zone,
                devices,
                clock_ready=lambda: ready is None or ready.exists(),
            ) as scheduler,
            HomeServer(
                (config.listen, config.port), HomeAPI(devices, scheduler), token, tls
            ) as server,
        ):
            LOG.info("Home service listening on %s:%s", config.listen, config.port)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, sqlite3.Error) as error:
        LOG.error("Could not start home service: %s", error)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
