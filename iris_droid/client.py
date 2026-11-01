"""Droid-side microphone, optional local wake detection, and speaker playback."""

from __future__ import annotations

import argparse
import base64
import os
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from iris_droid.capture import (
    DEFAULT_MICROPHONE_BUFFER,
    CheckedMicrophoneStream,
    validate_capture_options,
)
from iris_droid.feedback import ClientFeedback
from iris_droid.protocol import MAX_BODY, TurnReply, read_wav, validate_token, write_wav
from iris_droid.wake import WakeDetector

if TYPE_CHECKING:
    import speech_recognition as sr


class DroidClient:
    def __init__(self, url: str, token: str, client: httpx.Client) -> None:
        parts = urlsplit(url)
        _ = parts.port
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or parts.path not in {"", "/"}
        ):
            raise ValueError(
                "Use the Iris droid server's http(s) origin, e.g. http://192.168.1.10:8765"
            )
        validate_token(token)
        self.url = url.rstrip("/")
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}", "X-Iris-Session": str(uuid4())}

    def turn(self, body: bytes, *, text_only: bool = False) -> TurnReply:
        if not 0 < len(body) <= MAX_BODY:
            raise ValueError("Turn is empty or exceeds the transport size limit")
        headers = {
            **self.headers,
            "Content-Type": "text/plain" if text_only else "audio/wav",
        }
        route = "text" if text_only else "voice"
        with self.client.stream(
            "POST", f"{self.url}/v1/{route}", content=body, headers=headers
        ) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise ValueError("Server response exceeds the transport size limit")
        return TurnReply.model_validate_json(bytes(data))


def play_wav(data: bytes, device: int | None = None, *, loop: bool = False) -> None:
    import numpy as np
    import sounddevice as sd
    import speech_recognition as sr

    pcm, rate = read_wav(data)
    try:
        try:
            sd.check_output_settings(device=device, channels=1, dtype="int16", samplerate=rate)
        except sd.PortAudioError:
            # Direct ALSA hardware often accepts only 44.1/48 kHz. Convert the
            # samples too: changing just the rate would change pitch and speed.
            output_rate = int(sd.query_devices(device, "output")["default_samplerate"])
            sd.check_output_settings(
                device=device, channels=1, dtype="int16", samplerate=output_rate
            )
            pcm = sr.AudioData(pcm, rate, 2).get_raw_data(convert_rate=output_rate)
            rate = output_rate
        samples = np.frombuffer(pcm, dtype="<i2")
        if loop:
            sd.play(samples, rate, device=device, blocking=False, loop=True)
        else:
            sd.play(samples, rate, device=device, blocking=True)
    except sd.PortAudioError as error:
        raise OSError(f"Speaker {device if device is not None else 'default'}: {error}") from error


def stop_playback() -> None:
    import sounddevice as sd

    try:
        sd.stop()
    except sd.PortAudioError as error:
        raise OSError(f"Cannot stop feedback playback: {error}") from error


@contextmanager
def open_microphone(
    device: int | None,
    buffer_frames: int = DEFAULT_MICROPHONE_BUFFER,
    sample_rate: int | None = None,
) -> Iterator[sr.Microphone]:
    import speech_recognition as sr

    validate_capture_options(buffer_frames=buffer_frames, sample_rate=sample_rate)
    try:
        # Leave rate selection to PyAudio unless overridden. Conversion to the
        # server's 16 kHz format happens after recording.
        microphone = (
            sr.Microphone(device_index=device, chunk_size=buffer_frames)
            if sample_rate is None
            else sr.Microphone(
                device_index=device, chunk_size=buffer_frames, sample_rate=sample_rate
            )
        )
        source = microphone.__enter__()
    except (OSError, ValueError, AssertionError, AttributeError) as error:
        raise OSError(f"Cannot open microphone {device}: {error}") from error
    # Some SpeechRecognition versions swallow the opening error and terminate
    # PyAudio, leaving no stream. Their __exit__ then masks it with AttributeError.
    if source.stream is None:
        raise OSError(
            f"Cannot open microphone {device}: no capture stream. Check the device's "
            "input channels, supported sample rates and whether it is already in use. "
            f"Requested rate: {sample_rate or 'device default'}; buffer: {buffer_frames} frames."
        )
    try:
        source.stream = CheckedMicrophoneStream(source.stream)
        yield source
    finally:
        microphone.__exit__(None, None, None)


def record_turn(
    microphone: int | None,
    limit: float,
    timeout: float,
    wake: WakeDetector | None,
    speaker: int | None = None,
    buffer_frames: int = DEFAULT_MICROPHONE_BUFFER,
    pause_threshold: float = 1.2,
    *,
    sample_rate: int | None = None,
    energy_threshold: float | None = None,
    calibration_duration: float = 0.6,
    diagnostics: bool = False,
    feedback: ClientFeedback | None = None,
    announce_ready: bool = False,
) -> bytes:
    import speech_recognition as sr

    validate_capture_options(
        buffer_frames=buffer_frames,
        pause_threshold=pause_threshold,
        sample_rate=sample_rate,
        energy_threshold=energy_threshold,
        calibration_duration=calibration_duration,
    )
    recognizer = sr.Recognizer()
    recognizer.pause_threshold = pause_threshold
    with open_microphone(microphone, buffer_frames, sample_rate) as source:
        if energy_threshold is None:
            # Always read at least one buffer, even at low device rates or with
            # a large buffer. The library otherwise calibrates using no audio.
            duration = max(calibration_duration, source.CHUNK / source.SAMPLE_RATE * 1.01)
            recognizer.adjust_for_ambient_noise(source, duration=duration)
        else:
            recognizer.energy_threshold = energy_threshold
        # Some SpeechRecognition versions also adapt during the phrase. Steady
        # speech then raises the threshold above the voice and ends capture early.
        recognizer.dynamic_energy_threshold = False
        if diagnostics:
            print(
                f"Microphone: {source.SAMPLE_RATE} Hz, mono, {source.CHUNK} frames "
                f"({source.CHUNK / source.SAMPLE_RATE * 1000:.0f} ms); "
                f"speech threshold: {recognizer.energy_threshold:.0f}; "
                f"end pause: {pause_threshold:g} s.",
                flush=True,
            )
    # Ready means calibration is finished. In wake mode the next capture waits
    # for the wake phrase; cue playback never shares an open microphone.
    if announce_ready and feedback is not None:
        feedback.ready()
    if wake is not None:
        with open_microphone(microphone, buffer_frames, sample_rate) as source:
            wake.wait(source)
    if feedback is None:
        play_wav(files("iris_sounds").joinpath("bleep.wav").read_bytes(), speaker)
    else:
        feedback.listening()
    with open_microphone(microphone, buffer_frames, sample_rate) as source:
        print("Speak now...", flush=True)
        audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=limit)
    # SpeechRecognition's chunk boundaries can run slightly over its time cap.
    pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
    if feedback is not None:
        feedback.recorded()
    return write_wav(pcm[: int(limit * 16000) * 2], 16000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server", default="http://127.0.0.1:8765", help="Iris server URL (default: this machine)"
    )
    parser.add_argument("--list-microphones", action="store_true", help="List microphone indices")
    parser.add_argument("--text", action="store_true", help="Test transport without any audio")
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
    parser.add_argument("--microphone", type=int)
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
    parser.add_argument(
        "--test-microphone",
        action="store_true",
        help="Record and play one local test; no server, key, wake model or saved files needed",
    )
    parser.add_argument("--speaker", type=int, help="sounddevice output device index")
    parser.add_argument(
        "--microphone-buffer",
        type=int,
        default=DEFAULT_MICROPHONE_BUFFER,
        help="Capture buffer in frames (256–65536; default: 8192). Increase for input overflow.",
    )
    parser.add_argument("--phrase-time-limit", type=float, default=30)
    parser.add_argument("--listen-timeout", type=float, default=10)
    parser.add_argument(
        "--pause-threshold",
        type=float,
        default=1.2,
        help="Seconds of quiet that end a recording (0.5–10; default: 1.2)",
    )
    args = parser.parse_args(argv)
    try:
        if args.list_microphones:
            import speech_recognition as sr

            for index, name in enumerate(sr.Microphone.list_microphone_names()):
                print(f"{index}: {name}")
            return 0
        if not 0 < args.phrase_time_limit <= 300 or not 0 < args.listen_timeout <= 300:
            raise ValueError("Recording limit and listen timeout must be in (0, 300]")
        validate_capture_options(
            buffer_frames=args.microphone_buffer,
            pause_threshold=args.pause_threshold,
            sample_rate=args.microphone_rate,
            energy_threshold=args.energy_threshold,
            calibration_duration=args.calibration_duration,
        )
        with ClientFeedback(
            args.speaker, play_wav, stop_playback, enabled=not args.no_sounds and not args.text
        ) as feedback:
            if args.test_microphone:
                if args.text:
                    raise ValueError("--test-microphone cannot be combined with --text")
                print("Local microphone test. Stay quiet until the listening cue, then speak.")
                import speech_recognition as sr

                try:
                    body = record_turn(
                        args.microphone,
                        args.phrase_time_limit,
                        args.listen_timeout,
                        None,
                        args.speaker,
                        args.microphone_buffer,
                        args.pause_threshold,
                        sample_rate=args.microphone_rate,
                        energy_threshold=args.energy_threshold,
                        calibration_duration=args.calibration_duration,
                        diagnostics=True,
                        feedback=feedback,
                    )
                except sr.WaitTimeoutError:
                    feedback.error()
                    print(
                        "Didn't hear speech during the local test. "
                        "Check input gain and speech threshold."
                    )
                    return 1
                pcm, rate = read_wav(body, input_audio=True)
                print(
                    f"Recorded {len(pcm) / (rate * 2):.1f} seconds. Playing local test...",
                    flush=True,
                )
                play_wav(body, args.speaker)
                return 0
            with httpx.Client(
                trust_env=False, follow_redirects=False, timeout=httpx.Timeout(600, connect=10)
            ) as transport:
                client = DroidClient(args.server, os.environ.get("IRIS_DROID_TOKEN", ""), transport)
                wake = None
                if not args.text and args.activation == "wake":
                    if args.wake_model is None:
                        raise ValueError(
                            "Wake mode requires --wake-model with a local Vosk model folder"
                        )
                    wake = WakeDetector(args.wake_model, args.wake_phrase)
                print("Iris droid ready. Say/type exit to finish; Ctrl-C also stops the client.")
                while True:
                    if not args.text and args.activation == "enter":
                        feedback.ready()
                    if args.text:
                        body = input("You: ").encode()
                        if not body.strip():
                            continue
                    else:
                        import speech_recognition as sr

                        if args.activation == "enter":
                            input("Press Enter to talk...")
                        try:
                            body = record_turn(
                                args.microphone,
                                args.phrase_time_limit,
                                args.listen_timeout,
                                wake,
                                args.speaker,
                                args.microphone_buffer,
                                args.pause_threshold,
                                sample_rate=args.microphone_rate,
                                energy_threshold=args.energy_threshold,
                                calibration_duration=args.calibration_duration,
                                diagnostics=args.echo_recording,
                                feedback=feedback,
                                announce_ready=args.activation != "enter",
                            )
                        except sr.WaitTimeoutError:
                            feedback.error()
                            print("Didn't hear speech. Try again.")
                            continue
                        if args.echo_recording:
                            pcm, rate = read_wav(body, input_audio=True)
                            print(
                                f"Recorded {len(pcm) / (rate * 2):.1f} seconds. "
                                "Playing recording before sending...",
                                flush=True,
                            )
                            play_wav(body, args.speaker)
                    try:
                        with feedback.working():
                            reply = client.turn(body, text_only=args.text)
                    except httpx.HTTPError as error:
                        feedback.error()
                        status = (
                            str(error.response.status_code)
                            if isinstance(error, httpx.HTTPStatusError)
                            else type(error).__name__
                        )
                        print(
                            f"Droid connection/turn failed ({status}). "
                            "Check the PC; turn not resent."
                        )
                        continue
                    print(f"You: {reply.heard}\nIris: {reply.text}")
                    if reply.audio and not args.text:
                        play_wav(base64.b64decode(reply.audio, validate=True), args.speaker)
                    if reply.finished:
                        return 0
    except (KeyboardInterrupt, EOFError):
        return 0
    except (ValueError, OSError, ImportError) as error:
        print(f"Droid client setup/audio: {error}")
        return 1
    except Exception as error:
        print(f"Droid client stopped ({type(error).__name__}); check audio devices and setup.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
