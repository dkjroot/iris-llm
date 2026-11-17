"""Shared client defaults for the command line and the setup wizard."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, fields
from pathlib import Path

from iris_droid.capture import DEFAULT_MICROPHONE_BUFFER, validate_capture_options
from iris_droid.face_display import (
    DEFAULT_FACE_PORT,
    FACE_HEADS,
    FACE_IDLE_MODES,
    FaceHead,
    FaceIdle,
)
from iris_settings import read_config

DEFAULT_CLIENT_CONFIG = Path("client_config.json")


def audio_selector(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        if not value.strip():
            raise argparse.ArgumentTypeError("Enter a device index or name") from None
        return value.strip()


def settings_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--server", default="http://127.0.0.1:8765", help="Iris server URL (default: this machine)"
    )
    parser.add_argument(
        "--personalities-file", type=Path, help="JSON file with this client's personality prompts"
    )
    parser.add_argument("--face", choices=FACE_HEADS, help="Serve a face for the droid's display")
    parser.add_argument(
        "--face-idle", choices=FACE_IDLE_MODES, default="snow", help="Idle display (default: snow)"
    )
    parser.add_argument(
        "--face-port",
        type=int,
        default=DEFAULT_FACE_PORT,
        help=f"Loopback port for the face display (default: {DEFAULT_FACE_PORT})",
    )
    parser.add_argument(
        "--volume-backend",
        choices=["none", "alsa"],
        default="none",
        help="Client speaker mixer backend (default: disabled)",
    )
    parser.add_argument("--alsa-card", help="ALSA card number or ID; separate from --speaker")
    parser.add_argument("--alsa-control", default="Master", help="ALSA playback mixer control")
    parser.add_argument(
        "--no-sounds",
        action="store_true",
        help="Disable feedback cues and busy sound (spoken replies and recording echo still play)",
    )
    parser.add_argument(
        "--echo-recording",
        action="store_true",
        help="Play each captured recording locally before sending it (voice mode only)",
    )
    parser.add_argument("--activation", choices=["enter", "continuous", "wake"], default="enter")
    parser.add_argument("--wake-model", type=Path)
    parser.add_argument("--wake-phrase", default="hey iris")
    parser.add_argument(
        "--voice-cancel",
        action="store_true",
        help='Listen locally for "cancel, cancel" during processing (requires --wake-model)',
    )
    parser.add_argument("--vad-model", type=Path, help="Enable local Silero speech detection")
    parser.add_argument(
        "--vad-threshold",
        type=float,
        default=0.5,
        help="Speech probability needed to start a turn (0–1 exclusive; default: 0.5)",
    )
    parser.add_argument(
        "--conversation-timeout",
        type=float,
        default=300,
        help="Wake conversation idle timeout in seconds (0 < value <= 3600; default: 300)",
    )
    parser.add_argument("--microphone", type=audio_selector, help="PyAudio input index or name")
    parser.add_argument(
        "--microphone-rate",
        type=int,
        help="Capture rate in Hz (8000–192000; default: device rate). Must be supported by input.",
    )
    parser.add_argument(
        "--energy-threshold",
        type=float,
        help="Fixed speech energy threshold (0–32768, exclusive of 0); skips ambient calibration",
    )
    parser.add_argument(
        "--calibration-duration",
        type=float,
        default=0.6,
        help="Ambient calibration in seconds (0.1–10; default: 0.6, at least one input buffer)",
    )
    parser.add_argument("--speaker", type=audio_selector, help="sounddevice output index or name")
    parser.add_argument(
        "--speech-lead-in",
        type=float,
        default=0.3,
        help="Silence prepended in the reply playback stream (0–2 seconds; default: 0.3)",
    )
    parser.add_argument(
        "--speech-primer",
        action="store_true",
        help="Diagnostic: prepend a soft chirp in the same stream as spoken replies",
    )
    parser.add_argument(
        "--trace-voice",
        action="store_true",
        help="Report reply word count and timing from synthesis to speaker output",
    )
    parser.add_argument(
        "--no-speech-stream",
        action="store_true",
        help="Wait for the complete reply audio before playing it",
    )
    parser.add_argument(
        "--microphone-buffer",
        type=int,
        default=DEFAULT_MICROPHONE_BUFFER,
        help="Capture buffer in frames (256–65536; default: 8192). Increase for input overflow.",
    )
    parser.add_argument("--phrase-time-limit", type=float, default=60)
    parser.add_argument("--listen-timeout", type=float, default=10)
    parser.add_argument(
        "--companion-idle-min",
        type=float,
        default=90,
        help="Shortest companion silence before an opener, in seconds (default: 90)",
    )
    parser.add_argument(
        "--companion-idle-max",
        type=float,
        default=240,
        help="Longest initial companion silence, in seconds (default: 240; backs off if ignored)",
    )
    parser.add_argument(
        "--pause-threshold",
        type=float,
        default=1.2,
        help="Seconds of quiet that end a recording (0.5–10; default: 1.2)",
    )
    return parser


def client_parser(settings: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Droid-side microphone, local wake detection and speaker playback.",
        parents=[settings],
    )
    parser.add_argument(
        "--config", type=Path, help="Client settings file (default: ./client_config.json)"
    )
    parser.add_argument("--list-microphones", action="store_true", help="List microphone indices")
    parser.add_argument("--text", action="store_true", help="Test transport without any audio")
    parser.add_argument(
        "--download-vad-model",
        type=Path,
        metavar="PATH",
        help="Download the pinned Silero model here and exit (no microphone or server)",
    )
    parser.add_argument(
        "--test-microphone",
        action="store_true",
        help="Record and play one local test; no server, key, wake model or saved files needed",
    )
    parser.add_argument(
        "--save-reply",
        type=Path,
        help="Diagnostic: save the unmodified reply WAV here, replacing it each voice turn",
    )
    parser.add_argument(
        "--play-wav",
        type=Path,
        help="Play a local WAV with reply playback settings, without server or microphone",
    )
    parser.add_argument(
        "--show-private-text",
        action="store_true",
        help="Display private conversation text in this terminal (may be logged)",
    )
    return parser


def validate_client_options(args: argparse.Namespace | ClientOptions) -> None:
    if not 0 < args.phrase_time_limit <= 300 or not 0 < args.listen_timeout <= 300:
        raise ValueError("Recording limit and listen timeout must be in (0, 300]")
    if not 0 <= args.speech_lead_in <= 2:
        raise ValueError("Speech lead-in must be between 0 and 2 seconds")
    if not 0 < args.vad_threshold < 1:
        raise ValueError("--vad-threshold must be between 0 and 1, exclusive")
    if args.face and not 1 <= args.face_port <= 65535:
        raise ValueError("--face-port must be between 1 and 65535")
    validate_capture_options(
        buffer_frames=args.microphone_buffer,
        pause_threshold=args.pause_threshold,
        sample_rate=args.microphone_rate,
        energy_threshold=args.energy_threshold,
        calibration_duration=args.calibration_duration,
    )


def config_options(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    # The settings parser contains only options that can be saved.
    return {action.dest: action for action in parser._actions}


def parse_config_value(action: argparse.Action, value: object) -> object:
    if value is None and action.default is None:
        return None
    accepted: tuple[type, ...]
    if isinstance(action.default, bool):
        accepted = (bool,)
    elif action.type is int:
        accepted = (int,)
    elif action.type is float:
        accepted = (float, int)
    elif action.type is audio_selector:
        accepted = (str, int)
    else:
        accepted = (str,)
    if type(value) not in accepted:
        types = " or ".join(kind.__name__ for kind in accepted)
        raise ValueError(f"{action.dest} must be {types}")
    if action.choices is not None and value not in action.choices:
        choices = ", ".join(map(str, action.choices))
        raise ValueError(f"{action.dest} must be one of {choices}")
    if callable(action.type):
        try:
            return action.type(str(value))
        except (argparse.ArgumentTypeError, ValueError) as error:
            raise ValueError(f"{action.dest}: {error}") from error
    return value


def config_values(
    data: dict[str, object], options: dict[str, argparse.Action]
) -> dict[str, object]:
    try:
        return {name: parse_config_value(options[name], value) for name, value in data.items()}
    except KeyError as error:
        raise ValueError(f"Unknown client setting {error.args[0]!r}") from error


def load_config(path: Path, options: dict[str, argparse.Action]) -> dict[str, object]:
    try:
        return config_values(read_config(path), options)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error


def parse_client_args(argv: list[str] | None = None) -> argparse.Namespace:
    settings = settings_parser()
    parser = client_parser(settings)
    cli = parser.parse_args(argv)
    if cli.list_microphones or cli.download_vad_model is not None:
        return cli
    path = cli.config
    if path is None:
        if not DEFAULT_CLIENT_CONFIG.exists():
            return cli
        path = DEFAULT_CLIENT_CONFIG
    values = load_config(path, config_options(settings))
    if cli.text or cli.test_microphone or cli.play_wav is not None:
        # Inherit audio tuning for diagnostics without enabling the wake listener.
        values.pop("voice_cancel", None)
    return parser.parse_args(argv, namespace=argparse.Namespace(**values))


@dataclass
class ClientOptions:
    """Validated startup options; argparse stays at the command-line boundary."""

    server: str
    personalities_file: Path | None
    face: FaceHead | None
    face_idle: FaceIdle
    face_port: int
    volume_backend: str
    alsa_card: str | None
    alsa_control: str
    no_sounds: bool
    echo_recording: bool
    activation: str
    wake_model: Path | None
    wake_phrase: str
    voice_cancel: bool
    vad_model: Path | None
    vad_threshold: float
    conversation_timeout: float
    microphone: int | str | None
    microphone_rate: int | None
    energy_threshold: float | None
    calibration_duration: float
    speaker: int | str | None
    speech_lead_in: float
    speech_primer: bool
    trace_voice: bool
    no_speech_stream: bool
    microphone_buffer: int
    phrase_time_limit: float
    listen_timeout: float
    companion_idle_min: float
    companion_idle_max: float
    pause_threshold: float
    config: Path | None
    list_microphones: bool
    text: bool
    download_vad_model: Path | None
    test_microphone: bool
    save_reply: Path | None
    play_wav: Path | None
    show_private_text: bool

    @classmethod
    def from_namespace(cls, args: argparse.Namespace) -> ClientOptions:
        validate_client_options(args)
        return cls(**{field.name: getattr(args, field.name) for field in fields(cls)})
