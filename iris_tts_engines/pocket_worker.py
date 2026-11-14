"""Run with the Pocket TTS Python interpreter."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from contextlib import redirect_stdout
from pathlib import Path
from typing import Protocol, TextIO, cast

MODEL_REPO = "openensemble/pocket-tts"
MODEL_REVISION = "0c22ea94220f3f98cad12df9a21e1158daa20037"
TOKENIZER_REPO = "kyutai/pocket-tts-without-voice-cloning"
TOKENIZER_REVISION = "00eac05ed3d16bdc3f6b5d598874019c34a89214"
LANGUAGE_DIR = "languages/english"
CHUNK_LIMIT = 9_000_000


class Waveform(Protocol):
    def numpy(self) -> object: ...


class Model(Protocol):
    sample_rate: int

    def get_state_for_audio_prompt(self, path: Path, truncate: bool = False) -> object: ...
    def generate_audio_stream(self, state: object, text: str) -> Iterator[Waveform]: ...


def download(directory: Path) -> None:
    from huggingface_hub import hf_hub_download

    for repo, revision, filename in (
        (MODEL_REPO, MODEL_REVISION, "model.safetensors"),
        (TOKENIZER_REPO, TOKENIZER_REVISION, "tokenizer.json"),
    ):
        hf_hub_download(repo, f"{LANGUAGE_DIR}/{filename}", revision=revision, local_dir=directory)
    print(f"Pocket TTS model ready in {directory}")


def keep_inference_local(event: str, _args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError(
            "Network access is disabled during Pocket TTS synthesis and preparation."
        )


def load_model(directory: Path) -> Model:
    import pocket_tts  # type: ignore[import-not-found]
    import torch
    import yaml  # type: ignore[import-untyped]

    weights = directory / LANGUAGE_DIR / "model.safetensors"
    tokenizer = directory / LANGUAGE_DIR / "tokenizer.json"
    if not weights.is_file() or not tokenizer.is_file():
        raise ValueError("Pocket TTS files are missing. Run iris voice --engine pocket download.")
    source = Path(pocket_tts.__file__).parent / "config/english.yaml"
    config = yaml.safe_load(source.read_text(encoding="utf-8"))
    config["weights_path"] = str(weights)
    config["weights_path_without_voice_cloning"] = None
    config["flow_lm"]["lookup_table"]["tokenizer_path"] = str(tokenizer)
    torch.set_num_threads(2)
    with tempfile.TemporaryDirectory(dir=directory) as staging:
        path = Path(staging) / "english.yaml"
        path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
        return cast(Model, pocket_tts.TTSModel.load_model(config=path))


def prepare(model: Model, reference: Path, voice: Path) -> None:
    from pocket_tts import export_model_state

    if voice.exists():
        raise ValueError(f"A prepared voice already exists at {voice}.")
    if reference.suffix.lower() != ".wav":
        raise ValueError("Supply a WAV file for voice preparation.")
    state = model.get_state_for_audio_prompt(reference, truncate=True)
    voice.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=voice.parent) as staging:
        temporary = Path(staging) / "voice.safetensors"
        export_model_state(state, temporary)
        if os.name == "nt":
            temporary.rename(voice)
        else:
            os.link(temporary, voice)
    print(f"Prepared voice saved to {voice}")


def serve(model: Model, voice: Path, output: TextIO) -> None:
    import numpy as np

    if model.sample_rate != 24000:
        raise ValueError("Expected a 24 kHz Pocket TTS model.")
    state = model.get_state_for_audio_prompt(voice)

    def emit(status: str, samples: str = "") -> None:
        print(json.dumps({"status": status, "samples": samples}), file=output, flush=True)

    emit("ready")
    for line in sys.stdin:
        try:
            text = json.loads(line)
            if not isinstance(text, str):
                raise ValueError("Expected speech text")
            size = 0
            for chunk in model.generate_audio_stream(state, text):
                samples = np.asarray(chunk.numpy(), dtype=np.float32).reshape(-1)
                if not samples.size or not np.isfinite(samples).all():
                    raise ValueError("Invalid Pocket TTS audio chunk")
                pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
                size += len(pcm)
                if size > CHUNK_LIMIT:
                    raise ValueError("Pocket TTS reply exceeds the transport limit")
                emit("audio", base64.b64encode(pcm).decode("ascii"))
            if not size:
                raise ValueError("Pocket TTS returned no audio")
            emit("done")
        except Exception as error:
            print(f"Pocket TTS synthesis failed ({type(error).__name__})", file=sys.stderr)
            emit("error")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("download", "prepare", "serve"))
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--voice", type=Path)
    args = parser.parse_args()
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    try:
        if args.action == "download":
            download(args.model_dir)
            return 0
        os.environ["HF_HUB_OFFLINE"] = "1"
        sys.addaudithook(keep_inference_local)
        if args.voice is None:
            raise ValueError("Supply a prepared voice path.")
        output = sys.stdout
        with redirect_stdout(sys.stderr):
            model = load_model(args.model_dir)
            if args.action == "prepare":
                if args.reference is None:
                    raise ValueError("Supply a reference WAV.")
                prepare(model, args.reference, args.voice)
            else:
                serve(model, args.voice, output)
        return 0
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(f"Pocket TTS: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
