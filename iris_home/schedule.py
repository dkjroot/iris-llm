"""Persistent weekly schedules and restart recovery."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Condition, Lock, Thread
from zoneinfo import ZoneInfo

from pydantic import TypeAdapter, ValidationError

from iris_home.devices import Device, DeviceError
from iris_home.protocol import DAYS, PowerState, Rule, ScheduleRequest

RULES = TypeAdapter(list[Rule])
LOG = logging.getLogger(__name__)


class ScheduleConflict(Exception):
    """A schedule edit was based on an older revision."""


def occurrences(
    rules: list[Rule], now: datetime, zone: ZoneInfo
) -> Iterator[tuple[datetime, Rule]]:
    """Find each weekday rule's nearest valid past and future occurrence.

    Search weekly candidates rather than a window relative to restart time.
    A nonexistent wall time skips that week, not the previous valid action.
    The one-year bound also covers historical timezone changes that skip a day.
    """
    local_day = now.astimezone(zone).date()
    for rule in rules:
        hour, minute = map(int, rule.time.split(":"))
        for weekday in rule.days:
            offset = (DAYS.index(weekday) - local_day.weekday()) % 7
            for direction in (-1, 1):
                for week in range(54):
                    day = local_day + timedelta(days=offset + direction * 7 * week)
                    wall = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
                    instant = wall.astimezone(UTC)
                    # fold=0 selects the first occurrence of a repeated wall time.
                    if instant.astimezone(zone) != wall:
                        continue
                    if (direction < 0 and instant <= now) or (direction > 0 and instant > now):
                        yield instant, rule
                        break
                else:
                    raise ValueError("No valid weekly schedule occurrence within a year")


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
        self._device_locks = {name: Lock() for name in devices}
        self._errors: dict[str, str] = {}
        self._generation = 0
        self._closed = False
        self._clock_synchronized = False
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                device TEXT PRIMARY KEY,
                rules TEXT NOT NULL,
                revision INTEGER NOT NULL,
                cursor TEXT NOT NULL
            )
        """)
        self.worker = Thread(target=self._run, name="home-scheduler", daemon=True)

    def __enter__(self) -> Scheduler:
        self.worker.start()
        return self

    def __exit__(self, *_: object) -> None:
        with self.condition:
            self.stopping = True
            self.condition.notify()
        if self.worker.ident is not None:
            self.worker.join(timeout=10)
        with self.condition:
            if not self.worker.is_alive() and not self._closed:
                self.db.close()
                self._closed = True
            elif self.worker.is_alive():
                self._errors["worker"] = "shutdown_timeout"
                LOG.error("Home scheduler is still waiting for a device during shutdown")

    def health(self) -> dict[str, object]:
        with self.condition:
            if self.stopping:
                state = "stopping"
            elif not self.worker.is_alive():
                state = "stopped"
            elif self._errors:
                state = "degraded"
            else:
                state = "ready" if self._clock_synchronized else "waiting_for_clock"
            return {"state": state, "errors": dict(self._errors)}

    def read(self, device: str) -> dict[str, object]:
        with self.condition:
            row = self.db.execute(
                "SELECT rules, revision FROM schedules WHERE device = ?",
                (device,),
            ).fetchone()
        try:
            rules, revision = (RULES.validate_json(row[0]), row[1]) if row else ([], 0)
        except (ValueError, TypeError):
            with self.condition:
                self._errors[device] = "invalid_schedule"
            raise DeviceError(
                "The saved schedule is invalid; repair or replace it before use."
            ) from None
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
            row = self.db.execute(
                "SELECT revision, rules, cursor FROM schedules WHERE device=?", (device,)
            ).fetchone()
            corrupt = False
            if row is not None:
                try:
                    RULES.validate_json(row[1])
                    parsed_cursor = datetime.fromisoformat(row[2])
                    if parsed_cursor.tzinfo is None:
                        raise ValueError("Schedule cursor needs a timezone")
                except (ValueError, TypeError, ValidationError):
                    corrupt = True
            current_rev = int(row[0]) if row else 0
            if current_rev != request.revision and not (corrupt and request.revision == 0):
                raise ScheduleConflict("The schedule changed. Read it again before editing.")
            next_revision = current_rev + 1
            now_utc = self.clock().astimezone(UTC).isoformat()
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
                        next_revision,
                        now_utc,
                    ),
                )
            self._generation += 1
            self._errors.pop(device, None)
            self.condition.notify()
            return self.read(device)

    def switch(self, device: str, state: PowerState) -> None:
        # Device ordering is distinct from database access. Reads and schedule
        # edits remain available while a controller is waiting for a plug.
        lock = self._device_locks[device]
        if not lock.acquire(timeout=10):
            raise DeviceError("The device is busy. Check its state before retrying.")
        try:
            now = self.clock().astimezone(UTC)
            self.devices[device].switch.set_state(state)
            with self.condition:
                if not self.clock_ready():
                    return
                row = self.db.execute(
                    "SELECT cursor FROM schedules WHERE device = ?", (device,)
                ).fetchone()
                if row is not None:
                    try:
                        existing = datetime.fromisoformat(row[0])
                        should_advance = existing.tzinfo is not None and existing < now
                    except (ValueError, TypeError):
                        should_advance = False
                    if should_advance:
                        with self.db:
                            self.db.execute(
                                "UPDATE schedules SET cursor = ? WHERE device = ? AND cursor = ?",
                                (now.isoformat(), device, row[0]),
                            )
                self._generation += 1
                self.condition.notify()
        finally:
            lock.release()

    def _next_delay(self, now: datetime) -> float:
        delay = 60.0
        with self.condition:
            rows = self.db.execute("SELECT device,rules,cursor,revision FROM schedules").fetchall()
        for device, raw_rules, raw_cursor, revision in rows:
            if device not in self.devices:
                continue
            try:
                cursor = datetime.fromisoformat(raw_cursor)
                if cursor.tzinfo is None:
                    raise ValueError("Schedule cursor needs a timezone")
                due: tuple[datetime, Rule] | None = None
                for instant, rule in occurrences(RULES.validate_json(raw_rules), now, self.zone):
                    if instant <= cursor:
                        continue
                    if instant > now:
                        delay = min(delay, (instant - now).total_seconds())
                    elif due is None or instant > due[0]:
                        due = (instant, rule)
            except (ValueError, TypeError, ValidationError):
                with self.condition:
                    self._errors[device] = "invalid_schedule"
                LOG.warning("Home scheduler rejected invalid persisted schedule state")
                delay = min(delay, 30.0)
                continue
            if due is None:
                continue
            instant, rule = due
            lock = self._device_locks[device]
            if not lock.acquire(blocking=False):
                delay = min(delay, 1.0)
                continue
            try:
                with self.condition:
                    current = self.db.execute(
                        "SELECT cursor,revision FROM schedules WHERE device=?", (device,)
                    ).fetchone()
                    if self.stopping or current != (raw_cursor, revision):
                        continue
                try:
                    self.devices[device].switch.set_state(rule.state)
                except Exception:
                    # A bad adapter must not silently kill scheduling for every device.
                    # Do not log its exception body, which can include credentials.
                    with self.condition:
                        self._errors[device] = "device_unconfirmed"
                    LOG.warning("Scheduled device action could not be confirmed; will retry")
                    delay = min(delay, 30.0)
                    continue
                with self.condition, self.db:
                    self.db.execute(
                        "UPDATE schedules SET cursor=? WHERE device=? AND revision=? AND cursor=?",
                        (instant.astimezone(UTC).isoformat(), device, revision, raw_cursor),
                    )
                    self._errors.pop(device, None)
            finally:
                lock.release()
        return delay

    def _run(self) -> None:
        try:
            while True:
                with self.condition:
                    if self.stopping:
                        return
                    generation = self._generation
                try:
                    ready = self.clock_ready()
                    with self.condition:
                        self._clock_synchronized = ready
                    delay = self._next_delay(self.clock()) if ready else 10.0
                    with self.condition:
                        self._errors.pop("worker", None)
                except Exception:
                    with self.condition:
                        self._errors["worker"] = "scheduler_failure"
                    LOG.warning("Home scheduler step failed; will retry")
                    delay = 30.0
                with self.condition:
                    if not self.stopping and generation == self._generation:
                        self.condition.wait(delay)
        finally:
            with self.condition:
                if self.stopping and not self._closed:
                    self.db.close()
                    self._closed = True
