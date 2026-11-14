"""Download speech weights or prepare a reusable voice from a WAV."""

import argparse
import re
from pathlib import Path

from iris_settings import ChatterboxSettings, PocketSettings, read_config
from iris_tts_engines import pocket
from iris_tts_engines.chatterbox import ROOT, VOICE_DIRECTORY, run_setup

VOICE_NAME = re.compile(r"[a-zA-Z0-9_-]{1,64}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--engine", choices=("chatterbox", "pocket"), default="chatterbox")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("download", help="Download the selected engine's model files")
    prepare = commands.add_parser("prepare", help="Prepare a voice from a WAV recording")
    prepare.add_argument("wav", type=Path)
    prepare.add_argument("name", help="Name to show in the Iris voice picker")
    args = parser.parse_args(argv)
    try:
        default = ROOT / "local-config.json"
        config = read_config(args.config or (default if default.is_file() else None))

        def setup(action: str, *options: str) -> None:
            if args.engine == "pocket":
                pocket_settings = PocketSettings.model_validate(
                    {name: config[name] for name in PocketSettings.model_fields if name in config}
                )
                pocket.run_setup(pocket_settings, action, *options)
            else:
                settings = ChatterboxSettings.model_validate(
                    {
                        name: config[name]
                        for name in ChatterboxSettings.model_fields
                        if name in config
                    }
                )
                run_setup(settings, action, *options)

        if args.command == "download":
            setup("download")
        else:
            name = args.name
            if not VOICE_NAME.fullmatch(name):
                raise ValueError("Use 1–64 letters, digits, hyphens or underscores for the name.")
            wav = args.wav.expanduser().resolve()
            if not wav.is_file():
                raise ValueError(f"WAV file not found: {wav}")
            suffix = ".safetensors" if args.engine == "pocket" else ".pt"
            destination = VOICE_DIRECTORY / f"{name}{suffix}"
            setup("prepare", "--reference", str(wav), "--voice", str(destination))
            print(f"Choose 'file:{name}' under {args.engine} in python iris.py configure.")
        return 0
    except (OSError, ValueError) as error:
        print(f"Iris voice setup: {error}")
        return 1
