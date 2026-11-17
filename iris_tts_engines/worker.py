"""Bounded pipe transport shared by isolated speech backends."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from io import BufferedReader
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from iris_frames import ndjson_lines
from iris_paths import runtime_data_root
from iris_tts_engines.text import plain_speech_text
from iris_turn import TurnExpired, check_deadline, remaining_timeout, turn_budget

ROOT = runtime_data_root()


VOICE_DIRECTORY = ROOT / "prepared-voices"


MAX_WORKER_FRAME = 30_001_024
SYNTHESIS_TIMEOUT = 180.0


def isolated_environment(*, download: bool = False) -> dict[str, str]:
    """Pass platform/model execution settings, not Iris service credentials."""
    allowed = {
        "PATH",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "APPDATA",
        "TMP",
        "TEMP",
        "TMPDIR",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "SYSTEMDRIVE",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "CUDA_VISIBLE_DEVICES",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "PYTORCH_CUDA_ALLOC_CONF",
        "PYTORCH_ALLOC_CONF",
    }
    if download:
        # Explicit model downloads retain their existing proxy/trust/auth setup.
        # These values are not needed by offline synthesis or voice preparation.
        allowed.update(
            {
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "HF_ENDPOINT",
                "HF_TOKEN",
                "REQUESTS_CA_BUNDLE",
                "SSL_CERT_FILE",
            }
        )
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


class Response(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ready", "audio", "done", "error"]
    protocol: int = 1
    request_id: int | None = None
    samples: str = ""
    rate: int = 0


def read_responses(stream: BufferedReader, responses: Queue[str | None], stop: Event) -> None:
    def put(line: str | None) -> None:
        while not stop.is_set():
            try:
                responses.put(line, timeout=0.1)
                return
            except Full:
                pass

    try:
        for line in ndjson_lines(
            iter(lambda: stream.read1(65536), b""), max_frame=MAX_WORKER_FRAME
        ):
            put(line)
            if stop.is_set():
                return
    except (OSError, ValueError):
        pass  # A malformed/oversized pipe is unusable, like an unexpected exit.
    finally:
        put(None)


Value = TypeVar("Value")


def wait_response(queue: Queue[Value], timeout: float) -> Value:
    """Poll cancellation while retaining one overall pipe deadline."""
    expires = monotonic() + timeout
    while True:
        check_deadline()
        remaining = expires - monotonic()
        if remaining <= 0:
            raise Empty
        try:
            return queue.get(timeout=remaining_timeout(min(0.1, remaining)))
        except Empty:
            continue


class SpeechWorker:
    """One request at a time; failed requests are never replayed.

    A later request may restart the process twice per rolling minute. Pipe writes
    run in a bounded writer thread, so a child that stops reading cannot hold the
    application indefinitely. Abandoned streams retire their pipe immediately.
    """

    def __init__(
        self, process: subprocess.Popen[bytes], responses: Queue[str | None], name: str
    ) -> None:
        self.process = process
        self.responses = responses
        self.name = name
        self.failed = False
        self.closed = False
        self._serial = Lock()
        self._number = 0
        self._active: int | None = None
        self._terminal = False
        self._expires = 0.0
        self._restart: Callable[[], None] | None = None
        self._restarts: list[float] = []
        self._writes: Queue[bytes | None] = Queue(maxsize=1)
        self._written: Queue[bool] = Queue(maxsize=1)

    def health(self) -> dict[str, object]:
        return {
            "state": "closed"
            if self.closed
            else "failed"
            if self.failed or self.process.poll() is not None
            else "ready",
            "busy": self._serial.locked(),
            "restarts": len(self._restarts),
        }

    def retire(self) -> None:
        if not self.failed:
            logging.getLogger("iris.performance").warning("iris_worker state=failed")
        self.failed = True
        if self.process.poll() is None:
            with suppress(OSError):
                self.process.kill()

    def receive(self, timeout: float) -> Response:
        try:
            if self._active is not None:
                remaining = self._expires - monotonic()
                if remaining <= 0:
                    raise TurnExpired("Speech worker response deadline expired")
                timeout = min(timeout, remaining)
            line = wait_response(self.responses, timeout)
            check_deadline()
            if line is None:
                raise RuntimeError(f"{self.name} stopped. Check the server console.")
            try:
                response = Response.model_validate_json(line)
            except ValidationError:
                raise RuntimeError(f"{self.name} returned an invalid response.") from None
            if response.protocol != 1 or response.request_id != self._active:
                raise RuntimeError(f"{self.name} returned an unrelated response.")
            if self._active is None and response.status != "ready":
                raise RuntimeError(f"{self.name} did not become ready.")
            if self._active is not None and response.status == "ready":
                raise RuntimeError(f"{self.name} restarted during synthesis.")
            if response.status in {"done", "error"}:
                self._terminal = True
            return response
        except Empty:
            self.retire()
            check_deadline()
            raise RuntimeError(f"{self.name} timed out.") from None
        except (RuntimeError, TurnExpired):
            self.retire()
            raise

    def send(self, text: str) -> None:
        check_deadline()
        self._number += 1
        self._active = self._number
        self._terminal = False
        payload = (
            json.dumps(
                {"protocol": 1, "request_id": self._active, "text": plain_speech_text(text)},
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        if len(payload) > 65536:
            raise ValueError("Speech request exceeds the worker limit")
        try:
            self._writes.put_nowait(payload)
            if not wait_response(self._written, 5):
                raise RuntimeError(f"{self.name} input pipe failed.")
            check_deadline()
        except TurnExpired:
            self.retire()
            raise
        except (Empty, Full, RuntimeError):
            self.retire()
            raise RuntimeError(f"{self.name} input pipe failed or timed out.") from None

    @contextmanager
    def request(self, text: str) -> Iterator[None]:
        if not self._serial.acquire(blocking=False):
            raise RuntimeError(f"{self.name} is already synthesizing a reply.")
        try:
            if self.closed:
                raise RuntimeError(f"{self.name} is closed.")
            if self.failed or self.process.poll() is not None:
                now = monotonic()
                self._restarts = [t for t in self._restarts if now - t < 60]
                if self._restart is None or len(self._restarts) >= 2:
                    raise RuntimeError(f"{self.name} recovery limit reached. Restart the server.")
                self._restarts.append(now)
                self._restart()
            self._expires = monotonic() + SYNTHESIS_TIMEOUT
            with turn_budget(SYNTHESIS_TIMEOUT):
                self.send(text)
                try:
                    yield
                except BaseException:
                    self.retire()
                    raise
                finally:
                    if not self._terminal:
                        self.retire()
        finally:
            self._serial.release()


Worker = TypeVar("Worker", bound=SpeechWorker)


@contextmanager
def open_worker(
    command: list[str], worker_type: type[Worker], name: str, env: dict[str, str]
) -> Iterator[Worker]:
    process: subprocess.Popen[bytes] | None = None
    reader: Thread | None = None
    writer: Thread | None = None
    stop = Event()
    tts: Worker | None = None

    def cleanup() -> None:
        stop.set()
        if process is None:
            return
        if process.poll() is None:
            with suppress(OSError):
                process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"{name} did not stop within the cleanup deadline") from None
        for thread in (reader, writer):
            if thread is not None and thread.ident is not None:
                thread.join(timeout=0.5)
        # Never acquire a stream lock held by a blocked pipe thread. Refuse further
        # restarts in that case, rather than accumulating abandoned threads.
        if any(t is not None and t.is_alive() for t in (reader, writer)):
            if tts is not None:
                tts.closed = True
            raise RuntimeError(f"{name} pipe threads did not stop")
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                with suppress(OSError):
                    stream.close()

    def launch() -> None:
        nonlocal process, reader, writer, stop, tts
        if process is not None:
            cleanup()
        check_deadline()
        stop = Event()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        responses: Queue[str | None] = Queue(maxsize=2)
        if tts is None:
            tts = worker_type(process, responses, name)
        else:
            tts.process, tts.responses = process, responses
            tts._writes = Queue(maxsize=1)
            tts._written = Queue(maxsize=1)
            tts._active = None
        tts.failed = True
        assert isinstance(process.stdout, BufferedReader)
        reader = Thread(target=read_responses, args=(process.stdout, responses, stop), daemon=True)
        reader.start()

        def write_requests() -> None:
            assert tts is not None and process is not None and process.stdin is not None
            while not stop.is_set():
                try:
                    data = tts._writes.get(timeout=0.1)
                except Empty:
                    continue
                if data is None:
                    return
                try:
                    process.stdin.write(data)
                    process.stdin.flush()
                except (OSError, ValueError):
                    tts._written.put_nowait(False)
                    return
                tts._written.put_nowait(True)

        writer = Thread(target=write_requests, daemon=True)
        writer.start()
        try:
            tts.receive(timeout=180)
        except RuntimeError as error:
            raise ValueError(str(error)) from error
        tts.failed = False

    try:
        launch()
        assert tts is not None
        tts._restart = launch
        yield tts
    finally:
        if tts is not None:
            tts.closed = True
        cleanup()
