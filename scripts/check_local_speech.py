"""Check real Kokoro -> PCM -> Whisper inference using disposable synthetic audio.

Install Iris's whisper and kokoro extras first. --prepare explicitly downloads
the models. The ordinary check uses only that directory and opens no audio device,
Iris configuration, conversation archive or model server.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

PROBE = "The curious robot reads a book beside the window."
WHISPER_REVISION = "0d3d19a32d3338f10357c0889762bd8d64bbdeba"


def offline_inference(assets: Path, pocket_python: Path | None) -> None:
    import numpy as np
    from speech_recognition import AudioData

    from iris_settings import Settings
    from iris_stt_engines.whisper_offline import STT
    from iris_tts_engines.kokoro_offline import TTS

    def no_network(event: str, _args: tuple[object, ...]) -> None:
        if event in {"socket.connect", "socket.getaddrinfo"}:
            raise RuntimeError("Speech check attempted network access during inference")

    sys.addaudithook(no_network)
    settings = Settings(
        kokoro_model_dir=assets / "kokoro",
        kokoro_voice="bf_emma",
        whisper_model=str(assets / "whisper-tiny.en"),
        device="cpu",
        compute_type="int8",
        language="en",
    )
    started = time.monotonic()
    tts = TTS(settings)
    samples, rate = tts.synthesize(PROBE)
    assert rate == 24000 and samples.ndim == 1 and np.isfinite(samples).all()
    assert 1 < samples.size / rate < 30 and float(np.max(np.abs(samples))) > 0.01
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(str(assets / "synthetic-probe.wav"), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm)
    stt = STT(settings)
    transcript = stt.recognize(AudioData(pcm, rate, 2))
    words = set(re.findall(r"[a-z]+", transcript.lower()))
    assert {"robot", "book", "window"} <= words, f"Unexpected transcription: {transcript!r}"
    assert stt.recognize(AudioData(b"", 16000, 2)) == ""
    result = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "versions": {
            name: importlib.metadata.version(name)
            for name in ("faster-whisper", "kokoro-onnx", "onnxruntime", "numpy")
        },
        "prompt": PROBE,
        "transcript": transcript,
        "sample_rate": rate,
        "audio_seconds": samples.size / rate,
        "elapsed_seconds": time.monotonic() - started,
        "scope": "CPU synthetic round trip; no microphone, speaker or human quality judgement",
        "whisper_revision": WHISPER_REVISION,
        "assets_sha256": {},
    }
    if pocket_python is not None:
        from iris_tts_engines.pocket import WORKER, Pocket
        from iris_tts_engines.worker import isolated_environment, open_worker

        environment = isolated_environment()
        environment["HF_HOME"] = str(assets / "hub")
        environment["CUDA_VISIBLE_DEVICES"] = ""
        command = [str(pocket_python), "-I", "-u", str(WORKER)]
        model_args = ["--model-dir", str(assets / "pocket")]
        with tempfile.TemporaryDirectory(prefix="voice-", dir=assets) as temporary:
            voice_args = ["--voice", str(Path(temporary) / "synthetic.safetensors")]
            subprocess.run(
                [
                    *command,
                    "prepare",
                    *model_args,
                    *voice_args,
                    "--reference",
                    str(assets / "synthetic-probe.wav"),
                ],
                env=environment,
                check=True,
                timeout=180,
            )
            with open_worker(
                [*command, "serve", *model_args, *voice_args], Pocket, "Pocket smoke", environment
            ) as worker:
                audio, pocket_rate = worker.synthesize(PROBE)
                assert (
                    pocket_rate == 24000 and np.isfinite(audio).all() and audio.size > pocket_rate
                )
                pocket_pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
                recognized = stt.recognize(AudioData(pocket_pcm, pocket_rate, 2))
                assert {"robot", "book", "window"} <= set(re.findall(r"[a-z]+", recognized.lower()))
                result["pocket"] = {
                    "transcript": recognized,
                    "audio_seconds": audio.size / pocket_rate,
                }
        result["elapsed_seconds"] = time.monotonic() - started
    for folder in (assets / "kokoro", assets / "whisper-tiny.en"):
        for path in sorted(folder.iterdir()):
            if path.is_file():
                with path.open("rb") as source:
                    result["assets_sha256"][str(path.relative_to(assets))] = hashlib.file_digest(
                        source, "sha256"
                    ).hexdigest()
    (assets / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True, type=Path)
    parser.add_argument("--prepare", action="store_true", help="Download about 430 MB of weights")
    parser.add_argument("--pocket-python", type=Path, help="Also check an installed Pocket worker")
    parser.add_argument("--offline-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    assets = args.assets.resolve()
    assets.mkdir(parents=True, exist_ok=True)
    # Resolving a venv's Python symlink would bypass that environment's packages.
    pocket_python = args.pocket_python.absolute() if args.pocket_python else None
    # Settings gives environment values precedence. Never inherit a real setup.
    for key in list(os.environ):
        if key.startswith("IRIS_") or key in {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"}:
            os.environ.pop(key)
    os.environ["HF_HOME"] = str(assets / "hub")
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    if args.prepare:
        from faster_whisper.utils import download_model

        from iris_settings import Settings
        from iris_tts_engines.kokoro_offline import download_voices

        download_voices(Settings(kokoro_model_dir=assets / "kokoro"))
        download_model(
            "tiny.en",
            output_dir=str(assets / "whisper-tiny.en"),
            revision=WHISPER_REVISION,
            use_auth_token=False,
        )
        if pocket_python is not None:
            from iris_tts_engines.pocket import WORKER

            subprocess.run(
                [
                    str(pocket_python),
                    "-I",
                    str(WORKER),
                    "download",
                    "--model-dir",
                    str(assets / "pocket"),
                ],
                check=True,
                timeout=600,
            )
        print("Assets prepared; run again without --prepare to check offline inference.")
    elif args.offline_child:
        offline_inference(assets, pocket_python)
    else:
        os.environ["HF_HUB_OFFLINE"] = "1"
        (assets / "result.json").unlink(missing_ok=True)
        extra = ["--pocket-python", str(pocket_python)] if pocket_python else []
        subprocess.run(
            [
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--assets",
                str(assets),
                "--offline-child",
                *extra,
            ],
            check=True,
            timeout=600,
        )


if __name__ == "__main__":
    main()
