"""Keep Turbo's separate Python environment behind a local pipe."""

from __future__ import annotations

import base64
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import numpy.typing as npt

from iris_settings import ChatterboxSettings
from iris_tts_engines.worker import (
    MAX_WORKER_FRAME as MAX_WORKER_FRAME,
)
from iris_tts_engines.worker import (
    ROOT as ROOT,
)
from iris_tts_engines.worker import (
    VOICE_DIRECTORY as VOICE_DIRECTORY,
)
from iris_tts_engines.worker import (
    Response as Response,
)
from iris_tts_engines.worker import (
    SpeechWorker as SpeechWorker,
)
from iris_tts_engines.worker import (
    Worker as Worker,
)
from iris_tts_engines.worker import isolated_environment
from iris_tts_engines.worker import (
    open_worker as open_worker,
)
from iris_tts_engines.worker import (
    read_responses as read_responses,
)

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
        [*worker_command(settings, action), *args],
        env=worker_environment(download=action == "download"),
        check=False,
    )
    if result.returncode:
        raise ValueError("Chatterbox setup failed. See the error above.")


def worker_environment(*, download: bool = False) -> dict[str, str]:
    return {
        **isolated_environment(download=download),
        "HF_HOME": str(ROOT / ".cache" / "chatterbox-hub"),
    }


class Chatterbox(SpeechWorker):
    def synthesize(self, text: str) -> tuple[npt.NDArray[np.float32], int]:
        with self.request(text):
            reply = self.receive(timeout=180)
            if reply.status != "audio" or reply.rate <= 0:
                raise RuntimeError("Chatterbox could not synthesize this reply.")
            samples = np.frombuffer(base64.b64decode(reply.samples, validate=True), dtype="<f4")
            if not samples.size or not np.isfinite(samples).all() or not samples.any():
                raise RuntimeError("Chatterbox returned empty, invalid or silent audio.")
            if self.receive(timeout=180).status != "done":
                raise RuntimeError("Chatterbox did not finish its reply.")
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
