"""Persistent weekly schedules and restart recovery."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Condition, Thread
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter

from iris_home.devices import Device, DeviceError
from iris_home.protocol import DAYS, PowerState, Rule, ScheduleRequest

RULES = TypeAdapter(list[Rule])
LOG = logging.getLogger(__name__)


class ScheduleConflict(Exception):
    """A schedule edit was based on an older revision."""


def occurrences(
    rules: list[Rule], now: datetime, zone: ZoneInfo
) -> Iterator[tuple[datetime, Rule]]:
    """Cover a full week on either side of now, including a skipped DST hour."""
    local_day = now.astimezone(zone).date()
    for offset in range(-8, 9):
        day = local_day + timedelta(days=offset)
        weekday = DAYS[day.weekday()]
        for rule in rules:
            if weekday not in rule.days:
                continue
            hour, minute = map(int, rule.time.split(":"))
            wall = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
            instant = wall.astimezone(UTC)
            # Skip nonexistent local times and use the first occurrence of a repeated time.
            if instant.astimezone(zone) == wall:
                yield instant, rule


class Scheduler:
    def __init__(
        self,
        database: Path,
        zone: ZoneInfo,
        devices: Mapping[str, Device],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock_ready: Callable[[], bool] = lambda: True,
    ) -> None:
        self.zone = zone
        self.devices = devices
        self.clock = clock
        self.clock_ready = clock_ready
        self.condition = Condition()
        self.stopping = False
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                device TEXT PRIMARY KEY,
                rules TEXT NOT NULL,
                revision INTEGER NOT NULL,
                cursor TEXT NOT NULL
            )
        """)
        self.worker = Thread(target=self._run, name="home-scheduler")

    def __enter__(self) -> Scheduler:
        self.worker.start()
        return self

    def __exit__(self, *_: object) -> None:
        with self.condition:
            self.stopping = True
            self.condition.notify()
        self.worker.join()
        self.db.close()

    def read(self, device: str) -> dict[str, object]:
        with self.condition:
            row = self.db.execute(
                "SELECT rules, revision FROM schedules WHERE device = ?",
                (device,),
            ).fetchone()
        rules, revision = (RULES.validate_json(row[0]), row[1]) if row else ([], 0)
        return {
            "device": device,
            "timezone": self.zone.key,
            "revision": revision,
            "rules": [rule.model_dump() for rule in rules],
        }

    def save(self, device: str, request: ScheduleRequest) -> dict[str, object]:
        with self.condition:
            if not self.clock_ready():
                raise DeviceError("The home service is waiting for its clock to synchronise.")
            current = self.read(device)
            if current["revision"] != request.revision:
                raise ScheduleConflict("The schedule changed. Read it again before editing.")
            with self.db:
                self.db.execute(
                    """
                    INSERT INTO schedules VALUES (?, ?, ?, ?)
                    ON CONFLICT(device) DO UPDATE SET
                        rules=excluded.rules, revision=excluded.revision, cursor=excluded.cursor
                """,
                    (
                        device,
                        RULES.dump_json(request.rules).decode(),
                        request.revision + 1,
                        self.clock().isoformat(),
                    ),
                )
            self.condition.notify()
            return self.read(device)

    def switch(self, device: str, state: PowerState) -> None:
        with self.condition:
            now = self.clock()
            self.devices[device].switch.set_state(state)
            if not self.clock_ready():
                return
            # A successful manual command supersedes older, still-pending schedule events.
            with self.db:
                self.db.execute(
                    "UPDATE schedules SET cursor = ? WHERE device = ? AND cursor < ?",
                    (now.isoformat(), device, now.isoformat()),
                )

    def _next_delay(self, now: datetime) -> float:
        delay = 60.0  # Recheck the wall clock after corrections and long sleeps.
        rows = self.db.execute("SELECT device, rules, cursor FROM schedules").fetchall()
        for device, raw_rules, raw_cursor in rows:
            if device not in self.devices:
                continue
            cursor = datetime.fromisoformat(raw_cursor)
            due: tuple[datetime, Rule] | None = None
            for instant, rule in occurrences(RULES.validate_json(raw_rules), now, self.zone):
                if instant <= cursor:
                    continue
                if instant > now:
                    delay = min(delay, (instant - now).total_seconds())
                elif due is None or instant > due[0]:
                    due = (instant, rule)
            if due is None:
                continue
            instant, rule = due
            try:
                self.devices[device].switch.set_state(rule.state)
            except DeviceError:
                LOG.warning("Scheduled action for %s could not be confirmed. Will retry.", device)
                delay = min(delay, 30.0)
                continue
            with self.db:
                self.db.execute(
                    "UPDATE schedules SET cursor = ? WHERE device = ?",
                    (instant.isoformat(), device),
                )
        return delay

    def _run(self) -> None:
        with self.condition:
            while not self.stopping:
                try:
                    delay = self._next_delay(self.clock()) if self.clock_ready() else 10.0
                except sqlite3.Error:
                    LOG.exception("Could not access the schedule database. Will retry.")
                    delay = 30.0
                self.condition.wait(delay)
