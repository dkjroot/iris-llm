"""Pinned local MiniLM embeddings. Network access exists only in explicit preparation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4

import httpx
import numpy as np
import numpy.typing as npt

from iris_memory import database_path
from iris_settings import Settings

REVISION = "751bff37182d3f1213fa05d7196b954e230abad9"
MODEL_ID = f"Xenova/all-MiniLM-L6-v2@{REVISION}:int8:mean-l2:256:paired-chunks-v1"
DIMENSIONS = 384
FILES = {
    "model_quantized.onnx": (
        "onnx/model_quantized.onnx",
        22972370,
        "afdb6f1a0e45b715d0bb9b11772f032c399babd23bfc31fed1c170afc848bdb1",
    ),
    "tokenizer.json": (
        "tokenizer.json",
        711661,
        "da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0",
    ),
}
Vector = npt.NDArray[np.float32]


class EmbeddingError(RuntimeError):
    """A local embedding failure, safe to report without conversation text."""


class Encoder(Protocol):
    model_id: str
    dimensions: int

    def encode(self, texts: list[str]) -> Vector: ...


def model_directory(settings: Settings) -> Path:
    return (
        (
            settings.memory_embedding_model_dir
            or (database_path(settings).parent / "models" / "all-MiniLM-L6-v2")
        )
        .expanduser()
        .resolve()
    )


def verify(directory: Path) -> None:
    for name, (_, size, checksum) in FILES.items():
        path = directory / name
        if not path.is_file() or path.stat().st_size != size:
            raise EmbeddingError(
                "Embedding model missing or incomplete. "
                "Run iris memory download-model on the server."
            )
        with path.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != checksum:
            raise EmbeddingError(
                "Embedding model checksum mismatch. Run iris memory download-model again."
            )


def download_model(directory: Path, client: httpx.Client) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, (remote, size, checksum) in FILES.items():
        destination = directory / name
        if destination.is_file() and destination.stat().st_size == size:
            with destination.open("rb") as source:
                if hashlib.file_digest(source, "sha256").hexdigest() == checksum:
                    continue
        temporary = directory / f".{name}.{uuid4()}.part"
        try:
            total = 0
            digest = hashlib.sha256()
            with client.stream(
                "GET",
                f"https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/{REVISION}/{remote}",
                timeout=120,
            ) as response:
                response.raise_for_status()
                with temporary.open("xb") as output:
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > size:
                            raise EmbeddingError("Embedding model download exceeds its pinned size")
                        digest.update(chunk)
                        output.write(chunk)
            if total != size or digest.hexdigest() != checksum:
                raise EmbeddingError("Embedding model download failed checksum verification")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    (directory / "MODEL_INFO.json").write_text(
        json.dumps(
            {
                "model_id": MODEL_ID,
                "license": "Apache-2.0",
                "source": "https://huggingface.co/Xenova/all-MiniLM-L6-v2",
                "original": "https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class MiniLMEncoder:
    model_id = MODEL_ID
    dimensions = DIMENSIONS

    def __init__(self, directory: Path) -> None:
        verify(directory)
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as error:
            raise EmbeddingError(
                "Semantic memory needs the memory extra: python -m pip install -e '.[memory]'"
            ) from error
        ort.disable_telemetry_events()
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        try:
            self.session = ort.InferenceSession(
                str(directory / "model_quantized.onnx"),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as error:
            raise EmbeddingError("Couldn't load the local embedding model") from error

    def encode(self, texts: list[str]) -> Vector:
        if not texts:
            return np.empty((0, DIMENSIONS), dtype=np.float32)
        if len(texts) > 32 or any(len(text) > 8000 for text in texts):
            raise EmbeddingError("Embedding input exceeds the bounded batch size")
        try:
            batch = self.tokenizer.encode_batch(texts)
            data = {
                "input_ids": np.array([row.ids for row in batch], dtype=np.int64),
                "attention_mask": np.array([row.attention_mask for row in batch], dtype=np.int64),
                "token_type_ids": np.array([row.type_ids for row in batch], dtype=np.int64),
            }
            hidden = self.session.run(
                None, {field.name: data[field.name] for field in self.session.get_inputs()}
            )[0]
            mask = data["attention_mask"][:, :, None]
            pooled = (hidden * mask).sum(1) / np.maximum(mask.sum(1), 1)
            norm = np.linalg.norm(pooled, axis=1, keepdims=True)
            if (
                pooled.shape != (len(texts), DIMENSIONS)
                or not np.isfinite(pooled).all()
                or (norm <= 0).any()
            ):
                raise EmbeddingError("Invalid embedding output")
            return np.asarray(pooled / norm, dtype=np.float32)
        except EmbeddingError:
            raise
        except Exception as error:
            raise EmbeddingError("Local embedding inference failed") from error
