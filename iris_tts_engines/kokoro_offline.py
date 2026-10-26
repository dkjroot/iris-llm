"""Local CPU speech, with model downloads kept out of the conversation path."""

from __future__ import annotations

import hashlib
import logging
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import httpx
import numpy as np
import numpy.typing as npt

from iris_settings import Settings

RECOMMENDED_VOICES = {
    "af_heart": "Heart - American female; suggested starting voice",
    "bf_emma": "Emma - British female; suggested British starting voice",
    "bm_george": "George - British male",
    "bm_fable": "Fable - British male alternative",
    "af_bella": "Bella - American female alternative",
    "am_michael": "Michael - American male",
}
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str


ASSETS = (
    Asset(
        "kokoro-v1.0.onnx",
        325_532_387,
        "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    ),
    Asset(
        "voices-v1.0.bin",
        28_214_398,
        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
    ),
)


def model_directory(settings: Settings) -> Path:
    return (settings.kokoro_model_dir or Path.home() / ".cache" / "iris" / "kokoro").resolve()


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download_voices(settings: Settings, client: httpx.Client | None = None) -> None:
    directory = model_directory(settings)
    directory.mkdir(parents=True, exist_ok=True)
    print(f"Kokoro setup: downloading the CPU model and voice pack into {directory}")
    context = (
        nullcontext(client)
        if client is not None
        else httpx.Client(
            follow_redirects=True, trust_env=False, timeout=httpx.Timeout(120, connect=15)
        )
    )
    with context as downloader:
        for asset in ASSETS:
            target = directory / asset.name
            if (
                target.is_file()
                and target.stat().st_size == asset.size
                and file_hash(target) == asset.sha256
            ):
                print(f"Already checked: {asset.name}")
                continue
            temporary: Path | None = None
            try:
                print(f"Downloading {asset.name} ({asset.size / 1_000_000:.1f} MB)...", flush=True)
                digest = hashlib.sha256()
                total = 0
                with tempfile.NamedTemporaryFile(
                    dir=directory, prefix=".kokoro-", delete=False
                ) as output:
                    temporary = Path(output.name)
                    with downloader.stream("GET", f"{RELEASE}/{asset.name}") as response:
                        response.raise_for_status()
                        for chunk in response.iter_bytes(1024 * 1024):
                            total += len(chunk)
                            if total > asset.size:
                                raise ValueError(
                                    f"Unexpected size for {asset.name}; download not installed"
                                )
                            digest.update(chunk)
                            output.write(chunk)
                if total != asset.size or digest.hexdigest() != asset.sha256:
                    raise ValueError(
                        f"Checksum/size check failed for {asset.name}; retry --download-voices"
                    )
                temporary.replace(target)
            except httpx.HTTPError as error:
                raise ValueError(
                    f"Couldn't download {asset.name}; "
                    "check the connection and retry --download-voices"
                ) from error
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    print("Kokoro model and voice pack checked. Ordinary speech stays offline.")


class KokoroBackend(Protocol):
    def get_voices(self) -> list[str]: ...
    def create(
        self, text: str, voice: str, speed: float, lang: str, trim: bool
    ) -> tuple[npt.NDArray[np.float32], int]: ...


class PronunciationWarningFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Phonemizer compares punctuation-split pieces with reassembled lines,
        # so this summary can report more than 100% without missing speech.
        return not (
            record.levelno == logging.WARNING
            and record.msg == "words count mismatch on %s%% of the lines (%s/%s)"
        )


_pronunciation_warning_filter = PronunciationWarningFilter()


def load_backend(model: Path, voices: Path) -> KokoroBackend:
    from kokoro_onnx import Kokoro
    from onnxruntime import InferenceSession, disable_telemetry_events

    logging.getLogger("phonemizer").addFilter(_pronunciation_warning_filter)
    # An explicit session keeps CPU inference independent of ONNX_PROVIDER or
    # whichever accelerator packages someone installed for their other projects.
    disable_telemetry_events()
    session = InferenceSession(str(model), providers=["CPUExecutionProvider"])
    return cast(KokoroBackend, Kokoro.from_session(session, str(voices)))


def prepare_audio(samples: npt.NDArray[np.float32], rate: int) -> npt.NDArray[np.float32]:
    if rate <= 0 or samples.ndim != 1 or not samples.size or not np.isfinite(samples).all():
        raise ValueError("Kokoro returned invalid audio")
    peak = float(np.max(np.abs(samples)))
    if peak == 0:
        raise ValueError("Kokoro returned silent audio")
    active = np.flatnonzero(np.abs(samples) > max(0.00001, peak * 0.001))
    if not active.size:
        return samples
    # Kokoro's default trim can eat a quiet opening consonant. Keep a generous
    # lead-in instead, without making the user wait through seconds of padding.
    start = max(0, int(active[0]) - round(rate * 0.25))
    end = min(len(samples), int(active[-1]) + 1 + round(rate * 0.08))
    return samples[start:end]


class TTS:
    def __init__(self, settings: Settings) -> None:
        directory = model_directory(settings)
        for asset in ASSETS:
            path = directory / asset.name
            if not path.is_file() or path.stat().st_size != asset.size:
                raise ValueError(
                    f"Missing/incomplete Kokoro files in {directory}. "
                    "Run python iris.py --download-voices first."
                )
        self.backend = load_backend(directory / ASSETS[0].name, directory / ASSETS[1].name)
        self.voice = settings.kokoro_voice
        if self.voice not in self.backend.get_voices():
            raise ValueError(
                f"Kokoro voice {self.voice!r} isn't in this pack. Try af_heart or bf_emma."
            )
        self.speed = settings.kokoro_speed
        self.language = "en-gb" if self.voice.startswith("b") else "en-us"

    def speak(self, text: str) -> None:
        if not text.strip():
            return
        import sounddevice as sd

        audio, rate = self.synthesize(text)
        sd.play(audio, rate, blocking=True)

    def synthesize(self, text: str) -> tuple[npt.NDArray[np.float32], int]:
        """Generate samples without opening a speaker (also used by the droid server)."""
        samples, rate = self.backend.create(
            text, voice=self.voice, speed=self.speed, lang=self.language, trim=False
        )
        return prepare_audio(samples, rate), rate

    def close(self) -> None:
        import sounddevice as sd

        sd.stop()
