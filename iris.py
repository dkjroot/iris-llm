"""Iris Droid: one server, one client, on the same machine or across the network."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, ValidationError

from iris_chat_engines.backends import chat_headers
from iris_chat_engines.messages import ChatError
from iris_paths import server_config
from iris_settings import Settings, load_settings


def download_models(settings: Settings) -> None:
    print("Setup download: selected speech model files may be fetched from Hugging Face.")
    if settings.stt_engine == "whisper":
        from iris_stt_engines.whisper_offline import load_model

        load_model(settings, download=True)
    elif settings.stt_engine == "wav2vec2":
        from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

        Wav2Vec2Processor.from_pretrained(settings.tokenizer_model)
        Wav2Vec2ForCTC.from_pretrained(settings.ctc_model)
    else:
        print("Google recognition has no local model to download.")
    if settings.tts_engine == "chatterbox":
        from iris_tts_engines.chatterbox import run_setup

        run_setup(settings, "download")
    elif settings.tts_engine == "pocket":
        from iris_tts_engines.pocket import run_setup as pocket_setup

        pocket_setup(settings, "download")
    else:
        from iris_tts_engines.kokoro_offline import download_voices

        download_voices(settings)
    print("Done. Ordinary startup uses local/cached model files only.")


class AvailableModel(BaseModel):
    id: str = Field(min_length=1)


class ModelList(BaseModel):
    data: list[AvailableModel]


def check_setup(settings: Settings, client: httpx.Client) -> int:
    print("Checking the chat server (no conversation or audio is sent)...")
    try:
        headers = chat_headers(settings)
    except ChatError as error:
        print(error)
        return 1
    if settings.uses_openrouter:
        try:
            client.get(f"{settings.base_url}/key", timeout=10, headers=headers).raise_for_status()
        except httpx.HTTPError:
            print("Couldn't verify the OpenRouter key. Check IRIS_API_KEY and the connection.")
            return 1
    try:
        response = client.get(
            f"{settings.base_url}/models",
            timeout=10,
            headers=headers,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        print(
            "Couldn't list models. Check the address/API key and the server console. "
            "Some servers only implement chat; try --text if /models isn't supported."
        )
        return 1
    try:
        models = ModelList.model_validate_json(response.content)
    except ValidationError:
        print(
            "The server answered, but didn't return a standard model list. "
            "Check its console/model name and try --text."
        )
        return 1
    if not models.data:
        print("The server lists no models. Load your chat model in the server first.")
        return 1
    print("Available model IDs (use the appropriate ID in configure.py):")
    for model in models.data:
        print(f"  {model.id}")
    print("Next download the speech models, then start Iris with: python iris.py server")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    commands = ["server", "client", "configure", "memory", "voice"]
    if arguments and arguments[0] in commands:
        command, *remaining = arguments
        if command == "voice":
            from iris_tts_engines.voice_cli import main as voice_main

            return voice_main(remaining)
        if command == "memory":
            from iris_memory.cli import main as memory_main

            return memory_main(remaining)
        if command == "server":
            from iris_droid.server import main as server_main

            return server_main(remaining)
        if command == "client":
            from iris_droid.client import main as client_main

            return client_main(remaining)
        import configure

        return configure.main(remaining)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=commands)
    parser.add_argument("--config", type=Path, help="PC server configuration file")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true", help="Check the PC's model endpoint")
    actions.add_argument(
        "--download-models", action="store_true", help="Download server speech models"
    )
    actions.add_argument(
        "--download-voices", action="store_true", help="Download Kokoro voice files"
    )
    actions.add_argument("--list-voices", action="store_true", help="List Kokoro voice suggestions")
    args = parser.parse_args(arguments)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    if not any([args.check, args.download_models, args.download_voices, args.list_voices]):
        parser.print_help()
        return 0
    try:
        settings = load_settings(server_config(args.config))
        if args.list_voices:
            from iris_tts_engines.kokoro_offline import RECOMMENDED_VOICES

            for name, description in RECOMMENDED_VOICES.items():
                print(f"{name}: {description}")
        elif args.download_voices:
            from iris_tts_engines.kokoro_offline import download_voices

            download_voices(settings)
        elif args.download_models:
            download_models(settings)
        else:
            with httpx.Client(trust_env=False, follow_redirects=False) as client:
                return check_setup(settings, client)
        return 0
    except (KeyboardInterrupt, EOFError):
        return 0
    except (ValidationError, ValueError, OSError, ImportError) as error:
        print(f"Iris setup: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
