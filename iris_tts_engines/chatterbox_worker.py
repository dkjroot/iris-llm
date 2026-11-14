"""Run with the Chatterbox Python interpreter."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from pickle import UnpicklingError
from typing import BinaryIO, Protocol, TextIO, cast

MODEL_ID = "ResembleAI/chatterbox-turbo"
REVISION = "749d1c1a46eb10492095d68fbcf55691ccf137cd"
MODEL_FILES = (
    "conds.pt",
    "merges.txt",
    "vocab.json",
    "added_tokens.json",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "s3gen_meanflow.safetensors",
    "t3_turbo_v1.safetensors",
    "ve.safetensors",
)


class Voice(Protocol):
    def to(self, device: str) -> Voice: ...
    def save(self, stream: BinaryIO) -> None: ...


class Waveform(Protocol):
    def numpy(self) -> object: ...


class Model(Protocol):
    conds: Voice
    sr: int

    def prepare_conditionals(self, wav_fpath: str, exaggeration: float) -> None: ...
    def generate(self, text: str) -> Waveform: ...


def emit(stream: TextIO, payload: dict[str, str | int]) -> None:
    print(json.dumps(payload), file=stream, flush=True)


def serve(model: Model, output: TextIO) -> None:
    import numpy as np

    emit(output, {"status": "ready"})
    for line in sys.stdin:
        try:
            text = json.loads(line)
            if not isinstance(text, str):
                raise ValueError("Expected speech text")
            samples = np.asarray(model.generate(text).numpy(), dtype="<f4").reshape(-1)
            emit(
                output,
                {
                    "status": "audio",
                    "rate": model.sr,
                    "samples": base64.b64encode(samples.tobytes()).decode("ascii"),
                },
            )
        except Exception as error:
            print(f"Chatterbox synthesis failed ({type(error).__name__})", file=sys.stderr)
            emit(output, {"status": "error"})


def save_voice(voice: Voice, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=path.parent) as staging:
        temporary = Path(staging) / "voice.pt"
        with temporary.open("wb") as stream:
            voice.to("cpu").save(stream)
        # Publish the complete file without overwriting an existing voice.
        if os.name == "nt":
            temporary.rename(path)
        else:
            os.link(temporary, path)


def keep_inference_local(event: str, _args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError("Network access is disabled during Chatterbox synthesis and setup.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("download", "prepare", "serve"))
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--voice", type=Path)
    args = parser.parse_args()
    try:
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
        if args.action == "download":
            from huggingface_hub import snapshot_download

            snapshot_download(
                MODEL_ID,
                revision=REVISION,
                local_dir=args.model_dir,
                allow_patterns=list(MODEL_FILES),
            )
            print(f"Chatterbox Turbo model ready in {args.model_dir}")
            return 0
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["HF_DATASETS_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        sys.addaudithook(keep_inference_local)
        if any(not (args.model_dir / filename).is_file() for filename in MODEL_FILES):
            raise ValueError(
                f"Chatterbox model files missing in {args.model_dir}. Run iris voice download."
            )

        output = sys.stdout
        # PyTorch and the model use stdout for diagnostics. Reserve the original
        # stream for responses so the server never has to parse progress messages.
        with redirect_stdout(sys.stderr):
            import torch
            from chatterbox.tts_turbo import ChatterboxTurboTTS, Conditionals  # type: ignore[import-not-found]

            if args.device == "cuda" and not torch.cuda.is_available():
                raise ValueError("CUDA is unavailable. Check the PyTorch install or choose CPU.")
            if args.action == "prepare":
                import soundfile  # type: ignore[import-not-found]

                if args.reference is None or args.voice is None:
                    raise ValueError("Supply a WAV and a prepared voice destination.")
                if args.voice.exists():
                    raise ValueError(f"A prepared voice already exists at {args.voice}.")
                info = soundfile.info(args.reference)
                if info.format not in {"WAV", "WAVEX", "RF64"} or info.duration <= 5:
                    raise ValueError("Supply a WAV recording longer than five seconds.")

            model = cast(Model, ChatterboxTurboTTS.from_local(args.model_dir, args.device))
            if args.action == "prepare":
                model.prepare_conditionals(str(args.reference), exaggeration=0.0)
                save_voice(model.conds, args.voice)
                print(f"Prepared voice saved to {args.voice}")
            else:
                if args.voice is not None:
                    model.conds = cast(Voice, Conditionals.load(args.voice)).to(args.device)
                serve(model, output)
        return 0
    except ImportError as error:
        print(
            f"Could not import the Chatterbox dependencies: {error}. "
            "Check the separate Python environment.",
            file=sys.stderr,
        )
    except (OSError, RuntimeError, ValueError, UnpicklingError) as error:
        print(f"Chatterbox Turbo: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
