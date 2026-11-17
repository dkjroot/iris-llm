"""Synthetic scheduler/transport timing; never contacts a real device or service."""

from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
import tempfile
import time
from pathlib import Path
from threading import Event, Thread
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iris_home.devices import Device  # noqa: E402
from iris_home.schedule import Scheduler  # noqa: E402
from scripts.benchmark import benchmark, summarize  # noqa: E402


def scheduler_reads():
    class SlowSwitch:
        def __init__(self):
            self.entered = Event()

        def set_state(self, state):
            self.entered.set()
            time.sleep(0.1)

    results = {}
    with tempfile.TemporaryDirectory(prefix="iris-scheduler-benchmark-") as directory:
        switch = SlowSwitch()
        scheduler = Scheduler(
            Path(directory) / "schedule.sqlite3", ZoneInfo("UTC"), {"lamp": Device(switch, {})}
        )
        try:
            for hold_database_lock in (True, False):
                samples = []
                for _ in range(10):
                    switch.entered.clear()

                    def switch_once(hold=hold_database_lock):
                        if hold:
                            # Reproduce the old lock scope, with identical fake I/O.
                            with scheduler.condition:
                                scheduler.switch("lamp", "on")
                        else:
                            scheduler.switch("lamp", "on")

                    thread = Thread(target=switch_once)
                    thread.start()
                    assert switch.entered.wait(1)
                    started = time.perf_counter()
                    scheduler.read("lamp")
                    samples.append((time.perf_counter() - started) * 1000)
                    thread.join()
                results["old_lock_scope" if hold_database_lock else "separate_device_lock"] = (
                    summarize(samples)
                )
        finally:
            scheduler.db.close()
    return results


def transport_setup():
    """Only client-construction overhead; MockTransport performs no TCP/TLS."""
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": True}))
    samples = {}
    for persistent in (False, True):
        times = []
        with httpx.Client(transport=transport, trust_env=False) as shared:
            for _ in range(100):
                started = time.perf_counter()
                if persistent:
                    shared.get("https://synthetic.invalid")
                else:
                    with httpx.Client(transport=transport, trust_env=False) as client:
                        client.get("https://synthetic.invalid")
                times.append((time.perf_counter() - started) * 1000)
        samples["existing_persistent_client" if persistent else "client_per_call"] = summarize(
            times
        )
    return samples


class Events(logging.Handler):
    def __init__(self):
        super().__init__()
        self.events = []

    def emit(self, record):
        message = record.getMessage()
        if message.startswith("iris_stage "):
            self.events.append(json.loads(message.removeprefix("iris_stage ")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path(".cache/checks/resilience-benchmark.json")
    )
    args = parser.parse_args()
    handler = Events()
    logger = logging.getLogger("iris.performance")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    orchestration = benchmark(1000, 10)
    by_stage = {}
    turn_ids = {event["turn"] for event in handler.events if event["stage"] == "turn"}
    for event in handler.events:
        if event["turn"] not in turn_ids:
            continue
        by_stage.setdefault(event["stage"], []).append(event["duration_ms"])
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "note": "Fake serial inference and 100ms device calls. Transport excludes TCP/TLS. "
        "No physical audio playback is measured. Stage durations can be nested.",
        "scheduler_reads": scheduler_reads(),
        "transport_setup": transport_setup(),
        "stages": {name: summarize(values) for name, values in by_stage.items()},
        "orchestration": orchestration,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Synthetic resilience benchmark saved to {args.output}")


if __name__ == "__main__":
    main()
