"""Keep Turbo's separate Python environment behind a local pipe."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from typing import TypeVar

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ValidationError

from iris_settings import ChatterboxSettings
from iris_tts_engines.text import plain_speech_text

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIRECTORY = ROOT / "prepared-voices"
MODEL_DIRECTORY = ROOT / ".cache" / "chatterbox-turbo"
WORKER = Path(__file__).with_name("chatterbox_worker.py")


def worker_command(settings: ChatterboxSettings, action: str) -> list[str]:
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    python = (settings.chatterbox_python or ROOT / ".venv-chatterbox" / executable).expanduser()
    if not python.is_file():
        raise ValueError(
            "Install the Chatterbox environment or set chatterbox_python in configure."
        )
    model = settings.chatterbox_model_dir or MODEL_DIRECTORY
    return [
        str(python.absolute()),
        "-I",
        "-u",
        str(WORKER),
        action,
        "--model-dir",
        str(model.expanduser().resolve()),
        "--device",
        settings.chatterbox_device,
    ]


def run_setup(settings: ChatterboxSettings, action: str, *args: str) -> None:
    result = subprocess.run(
        [*worker_command(settings, action), *args], env=worker_environment(), check=False
    )
    if result.returncode:
        raise ValueError("Chatterbox setup failed. See the error above.")


def worker_environment() -> dict[str, str]:
    return {**os.environ, "HF_HOME": str(ROOT / ".cache" / "chatterbox-hub")}


class Response(BaseModel):
    status: str
    samples: str = ""
    rate: int = 0


def read_responses(stream: Iterator[str], responses: Queue[str | None]) -> None:
    try:
        for line in stream:
            responses.put(line)
    finally:
        responses.put(None)


class SpeechWorker:
    def __init__(
        self, process: subprocess.Popen[str], responses: Queue[str | None], name: str
    ) -> None:
        self.process = process
        self.responses = responses
        self.name = name

    def receive(self, timeout: float) -> Response:
        try:
            line = self.responses.get(timeout=timeout)
        except Empty as error:
            self.process.kill()
            raise RuntimeError(f"{self.name} timed out. Restart the Iris server.") from error
        if line is None:
            raise RuntimeError(f"{self.name} stopped. Check the server console.")
        try:
            return Response.model_validate_json(line)
        except ValidationError as error:
            raise RuntimeError(f"{self.name} returned an invalid response.") from error

    def send(self, text: str) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(plain_speech_text(text)) + "\n")
        self.process.stdin.flush()


class Chatterbox(SpeechWorker):
    def synthesize(self, text: str) -> tuple[npt.NDArray[np.float32], int]:
        self.send(text)
        reply = self.receive(timeout=180)
        if reply.status != "audio" or reply.rate <= 0:
            raise RuntimeError("Chatterbox could not synthesize this reply.")
        samples = np.frombuffer(base64.b64decode(reply.samples, validate=True), dtype="<f4")
        if not samples.size or not np.isfinite(samples).all() or not samples.any():
            raise RuntimeError("Chatterbox returned empty, invalid or silent audio.")
        return samples, reply.rate


@contextmanager
def open_chatterbox(settings: ChatterboxSettings) -> Iterator[Chatterbox]:
    command = worker_command(settings, "serve")
    if settings.chatterbox_voice is not None:
        voice = VOICE_DIRECTORY / f"{settings.chatterbox_voice}.pt"
        if not voice.is_file():
            raise ValueError(
                f"Prepared Chatterbox voice is missing: {voice}. Run iris voice prepare."
            )
        command.extend(("--voice", str(voice)))
    with open_worker(command, Chatterbox, "Chatterbox", worker_environment()) as tts:
        yield tts


Worker = TypeVar("Worker", bound=SpeechWorker)


@contextmanager
def open_worker(
    command: list[str], worker_type: type[Worker], name: str, env: dict[str, str]
) -> Iterator[Worker]:
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        encoding="utf-8",
        env=env,
    ) as process:
        assert process.stdout is not None
        responses: Queue[str | None] = Queue()
        reader = Thread(target=read_responses, args=(iter(process.stdout), responses), daemon=True)
        reader.start()
        tts = worker_type(process, responses, name)
        try:
            try:
                ready = tts.receive(timeout=180)
            except RuntimeError as error:
                raise ValueError(str(error)) from error
            if ready.status != "ready":
                raise ValueError(f"{name} could not start. Check the server console.")
            yield tts
        finally:
            assert process.stdin is not None
            with suppress(BrokenPipeError):
                process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            finally:
                reader.join()
