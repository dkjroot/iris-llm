"""Import an installed dependency profile without models, devices or configuration."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import sys
import traceback

PROFILES = {
    "base": {
        "httpx": "httpx",
        "pydantic": "pydantic",
        "pydantic-settings": "pydantic_settings",
        "SpeechRecognition": "speech_recognition",
        "numpy": "numpy",
        "sounddevice": "sounddevice",
        "tzdata": "tzdata",
    },
    "server": {
        "faster-whisper": "faster_whisper",
        "kokoro-onnx": "kokoro_onnx",
        "onnxruntime": "onnxruntime",
    },
    "memory": {"onnxruntime": "onnxruntime", "tokenizers": "tokenizers"},
    "vad": {"onnxruntime": "onnxruntime"},
    "microphone": {"PyAudio": "pyaudio"},
    "wake": {"vosk": "vosk"},
    "face": {"pygame": "pygame"},
    "wav2vec2": {"torch": "torch", "transformers": "transformers"},
    "kokoro": {"kokoro-onnx": "kokoro_onnx", "onnxruntime": "onnxruntime"},
    "chatterbox": {"chatterbox-tts": "chatterbox.tts_turbo"},
    "pocket": {"pocket-tts": "pocket_tts"},
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=PROFILES)
    args = parser.parse_args()
    failed = False
    # Worker environments are intentionally independent of Iris's base packages.
    packages = {} if args.profile in {"chatterbox", "pocket"} else dict(PROFILES["base"])
    packages.update(PROFILES[args.profile])
    for distribution, module in packages.items():
        try:
            version = importlib.metadata.version(distribution)
            importlib.import_module(module)
        except Exception as exc:
            print(f"FAIL {distribution}: {type(exc).__name__}")
            traceback.print_exc()
            failed = True
        else:
            print(f"OK {distribution} {version}")
    print("Import check only; model, GPU, device and end-to-end acceptance remain separate.")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
