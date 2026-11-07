"""Exercise the home service without a networked Matter device."""

import json
import subprocess
from datetime import UTC, datetime
from threading import Thread
from zoneinfo import ZoneInfo

import httpx
import pytest

from iris_home.devices import ChipTool, Device, DeviceError, MatterDeviceConfig, MatterPlug
from iris_home.protocol import ScheduleRequest
from iris_home.schedule import ScheduleConflict, Scheduler, occurrences
from iris_home.service import Config, HomeAPI, HomeServer
from iris_settings import Settings
from iris_tools import ToolRegistry

TOKEN = "a" * 32


class Switch:
    def __init__(self):
        self.commands = []
        self.offline = False

    def set_state(self, state):
        if self.offline:
            raise DeviceError("offline")
        self.commands.append(state)

    def state(self):
        return self.commands[-1] if self.commands else "off"


def plan(revision=0):
    return ScheduleRequest(
        device="lamp",
        revision=revision,
        rules=[
            {"days": ["sat"], "time": "18:00", "state": "on"},
            {"days": ["sat"], "time": "23:00", "state": "off"},
        ],
    )


def test_restart_catches_up_to_latest_missed_state_and_retries(tmp_path):
    instant = datetime(2026, 11, 7, 17, tzinfo=UTC)
    switch = Switch()
    db = tmp_path / "home.sqlite3"
    zone = ZoneInfo("Europe/London")
    devices = {"lamp": Device(switch, {})}
    with Scheduler(db, zone, devices, clock=lambda: instant) as scheduler:
        assert scheduler.save("lamp", plan())["revision"] == 1
        with pytest.raises(ScheduleConflict):
            scheduler.save("lamp", plan())

    instant = datetime(2026, 11, 8, 0, tzinfo=UTC)
    switch.offline = True
    # Drive the worker step with a deterministic clock and an offline plug.
    with Scheduler(db, zone, devices, clock_ready=lambda: False) as scheduler:
        with scheduler.condition:
            assert scheduler._next_delay(instant) == 30
            assert switch.commands == []
            switch.offline = False
            scheduler._next_delay(instant)
            scheduler._next_delay(instant)
        assert switch.commands == ["off"]
    with Scheduler(db, zone, devices, clock=lambda: instant) as scheduler:
        with scheduler.condition:
            scheduler._next_delay(instant)
        assert switch.commands == ["off"]


def test_schedule_uses_local_time_and_one_occurrence_in_repeated_hour():
    zone = ZoneInfo("Europe/London")
    rule = plan().rules[0].model_copy(update={"days": ["sun"], "time": "01:30"})
    autumn = datetime(2026, 10, 25, tzinfo=UTC)
    spring = datetime(2026, 3, 29, tzinfo=UTC)
    assert [
        instant
        for instant, _ in occurrences([rule], autumn, zone)
        if instant.date() == autumn.date()
    ] == [autumn.replace(minute=30)]
    assert all(instant.date() != spring.date() for instant, _ in occurrences([rule], spring, zone))


def test_conversation_tools_reach_authenticated_home_service(tmp_path):
    lamp = Switch()
    devices = {"Living room lamp": Device(lamp, {})}
    with Scheduler(tmp_path / "home.sqlite3", ZoneInfo("UTC"), devices) as scheduler:
        with HomeServer(("127.0.0.1", 0), HomeAPI(devices, scheduler), TOKEN, None) as service:
            thread = Thread(target=service.serve_forever)
            thread.start()
            try:
                url = f"http://127.0.0.1:{service.server_port}"
                with httpx.Client(trust_env=False) as client:
                    assert client.post(f"{url}/v1/switch", json={}).status_code == 401
                    registry = ToolRegistry(
                        Settings(home_service_url=url, home_service_token=TOKEN),
                        client,
                    )
                    assert "set_home_power" not in ToolRegistry(Settings(), client).skills
                    result = registry.execute(
                        "set_home_power",
                        '{"device":"living room lamp","state":"on"}',
                    )
                    assert json.loads(result)["accepted"] is True
                    assert lamp.commands == ["on"]
                    result = registry.execute(
                        "set_home_power",
                        '{"device":"other switch","state":"off"}',
                    )
                    assert "error" in json.loads(result)
                    result = registry.execute(
                        "save_home_schedule",
                        json.dumps({**plan().model_dump(), "device": "Living room lamp"}),
                    )
                    assert json.loads(result)["revision"] == 1
            finally:
                service.shutdown()
                thread.join()


def test_lan_binding_and_client_require_tls(tmp_path):
    with pytest.raises(ValueError, match="LAN listener requires"):
        Config(
            listen="192.168.1.12",
            database=tmp_path / "home.sqlite3",
            matter_storage=tmp_path,
            devices={"lamp": {"node": 1}},
        )
    with pytest.raises(ValueError, match="Use HTTPS"):
        Settings(home_service_url="http://192.168.1.12:8766", home_service_token=TOKEN)


def test_matter_adapter_translates_power_and_targets_configured_endpoint(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        output = {
            "active-power": "CHIP:TOO:   ActivePower: 4350\n",
            "cumulative-energy-imported": "CHIP:TOO:   { Energy: 1543000, EndTimestamp: 10 }\n",
            "on-off": "CHIP:TOO:   OnOff: TRUE\n",
            "voltage": "CHIP:TOO:   Voltage: null\n",
        }
        return subprocess.CompletedProcess(argv, 0, stdout=output.get(argv[3], ""))

    monkeypatch.setattr(subprocess, "run", run)
    plug = MatterPlug(
        ChipTool("/usr/bin/chip-tool", tmp_path),
        MatterDeviceConfig(
            node=91,
            switch_endpoint=1,
            power_endpoint=2,
            energy_endpoint=3,
        ),
    )
    assert plug.read("power") == {"metric": "power", "value": 4.35, "unit": "W"}
    assert calls[0][:6] == [
        "/usr/bin/chip-tool",
        "electricalpowermeasurement",
        "read",
        "active-power",
        "91",
        "2",
    ]
    plug.set_state("off")
    assert calls[1][1:5] == ["onoff", "off", "91", "1"]
    assert plug.state() == "on"
    assert plug.read("energy") == {"metric": "energy", "value": 1543.0, "unit": "Wh"}
    with pytest.raises(DeviceError, match="available meter"):
        plug.read("voltage")
