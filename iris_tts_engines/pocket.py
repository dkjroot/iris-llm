"""Talk to the CPU-only Pocket TTS worker through local pipes."""

import base64
import os
import subprocess
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from time import monotonic

import numpy as np
import numpy.typing as npt

from iris_settings import PocketSettings
from iris_tts_engines.worker import ROOT, VOICE_DIRECTORY, Response, SpeechWorker, open_worker
from iris_turn import TurnExpired

MODEL_DIRECTORY = ROOT / ".cache" / "pocket-tts"
WORKER = Path(__file__).with_name("pocket_worker.py")
SAMPLE_RATE = 24000


def worker_command(settings: PocketSettings, action: str) -> list[str]:
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    python = (settings.pocket_python or ROOT / ".venv-pocket" / executable).expanduser()
    if not python.is_file():
        raise ValueError("Install the Pocket TTS environment or set pocket_python in configure.")
    model = (settings.pocket_model_dir or MODEL_DIRECTORY).expanduser().resolve()
    return [str(python.absolute()), "-I", "-u", str(WORKER), action, "--model-dir", str(model)]


def worker_environment() -> dict[str, str]:
    return {
        **os.environ,
        "HF_HOME": str(ROOT / ".cache" / "pocket-hub"),
        "CUDA_VISIBLE_DEVICES": "",
    }


def run_setup(settings: PocketSettings, action: str, *args: str) -> None:
    result = subprocess.run(
        [*worker_command(settings, action), *args], env=worker_environment(), check=False
    )
    if result.returncode:
        raise ValueError("Pocket TTS setup failed. See the error above.")


class Pocket(SpeechWorker):
    sample_rate = SAMPLE_RATE

    def stream(self, text: str) -> Generator[bytes]:
        self.send(text)
        complete = False
        expires = monotonic() + 180
        size = 0

        def receive() -> Response:
            remaining = expires - monotonic()
            if remaining <= 0:
                self.process.kill()
                raise TurnExpired("Pocket TTS response deadline expired")
            return self.receive(timeout=remaining)

        try:
            while True:
                reply = receive()
                if reply.status in {"done", "error"}:
                    complete = True
                    if reply.status == "error":
                        raise RuntimeError("Pocket TTS could not finish this reply.")
                    return
                if reply.status != "audio":
                    raise RuntimeError("Pocket TTS returned an unexpected response.")
                pcm = base64.b64decode(reply.samples, validate=True)
                size += len(pcm)
                if size > 10_000_000:
                    self.process.kill()
                    raise RuntimeError("Pocket TTS reply exceeds the transport limit")
                if not pcm or len(pcm) % 2:
                    raise RuntimeError("Pocket TTS returned invalid PCM.")
                yield pcm
        finally:
            # A disconnected HTTP client stops receiving audio. Drain this request
            # before reusing the pipe so its remaining frames can't reach the next turn.
            if not complete and self.process.poll() is None:
                while receive().status not in {"done", "error"}:
                    pass

    def synthesize(self, text: str) -> tuple[npt.NDArray[np.float32], int]:
        pcm = b"".join(self.stream(text))
        if not pcm:
            raise RuntimeError("Pocket TTS returned no audio.")
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        samples /= np.float32(32767)
        return samples, self.sample_rate


@contextmanager
def open_pocket(settings: PocketSettings) -> Iterator[Pocket]:
    command = worker_command(settings, "serve")
    if settings.pocket_voice is None:
        raise ValueError(
            "Choose a Pocket TTS voice in configure. Run iris voice --engine pocket prepare."
        )
    voice = VOICE_DIRECTORY / f"{settings.pocket_voice}.safetensors"
    if not voice.is_file():
        raise ValueError(f"Prepared Pocket TTS voice is missing: {voice}")
    command.extend(("--voice", str(voice)))
    with open_worker(command, Pocket, "Pocket TTS", worker_environment()) as tts:
        yield tts
