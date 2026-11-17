"""Bounded pipe transport shared by isolated speech backends."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from io import BufferedReader
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from iris_frames import ndjson_lines
from iris_paths import runtime_data_root
from iris_tts_engines.text import plain_speech_text
from iris_turn import TurnExpired, check_deadline, remaining_timeout

ROOT = runtime_data_root()


VOICE_DIRECTORY = ROOT / "prepared-voices"


MAX_WORKER_FRAME = 30_001_024


class Response(BaseModel):
    status: str
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


class SpeechWorker:
    def __init__(
        self, process: subprocess.Popen[bytes], responses: Queue[str | None], name: str
    ) -> None:
        self.process = process
        self.responses = responses
        self.name = name

    def receive(self, timeout: float) -> Response:
        try:
            line = self.responses.get(timeout=remaining_timeout(timeout))
            check_deadline()
        except TurnExpired:
            self.process.kill()
            raise
        except Empty as error:
            self.process.kill()
            check_deadline()
            raise RuntimeError(f"{self.name} timed out. Restart the Iris server.") from error
        if line is None:
            self.process.kill()
            raise RuntimeError(f"{self.name} stopped. Check the server console.")
        try:
            return Response.model_validate_json(line)
        except ValidationError as error:
            self.process.kill()
            raise RuntimeError(f"{self.name} returned an invalid response.") from error

    def send(self, text: str) -> None:
        check_deadline()
        assert self.process.stdin is not None
        self.process.stdin.write((json.dumps(plain_speech_text(text)) + "\n").encode("utf-8"))
        self.process.stdin.flush()


Worker = TypeVar("Worker", bound=SpeechWorker)


@contextmanager
def open_worker(
    command: list[str], worker_type: type[Worker], name: str, env: dict[str, str]
) -> Iterator[Worker]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=env,
    )
    reader: Thread | None = None
    stop = Event()
    try:
        assert process.stdout is not None
        responses: Queue[str | None] = Queue(maxsize=2)
        assert isinstance(process.stdout, BufferedReader)
        reader = Thread(target=read_responses, args=(process.stdout, responses, stop), daemon=True)
        reader.start()
        tts = worker_type(process, responses, name)
        try:
            ready = tts.receive(timeout=180)
        except RuntimeError as error:
            raise ValueError(str(error)) from error
        if ready.status != "ready":
            raise ValueError(f"{name} could not start. Check the server console.")
        yield tts
    finally:
        stop.set()
        if process.stdin is not None:
            with suppress(OSError):
                process.stdin.close()
        try:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        finally:
            # A failed thread start has no reader to join. A reader still inside
            # an OS pipe retains its stream lock; never block cleanup on close().
            if reader is not None and reader.ident is not None:
                reader.join(timeout=1)
            if process.poll() is not None and (reader is None or not reader.is_alive()):
                if process.stdout is not None:
                    process.stdout.close()
