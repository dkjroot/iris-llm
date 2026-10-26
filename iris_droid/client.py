"""Droid-side microphone, optional local wake detection, and speaker playback."""

from __future__ import annotations

import argparse
import base64
import os
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from iris_droid.protocol import MAX_BODY, TurnReply, read_wav, write_wav
from iris_droid.wake import WakeDetector


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
        if len(token.strip()) < 24 or not token.isascii():
            raise ValueError("Set IRIS_DROID_TOKEN to the same secret as the PC server")
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


def play_wav(data: bytes, device: int | None = None) -> None:
    import numpy as np
    import sounddevice as sd

    pcm, rate = read_wav(data)
    sd.play(np.frombuffer(pcm, dtype="<i2"), rate, device=device, blocking=True)


def record_turn(
    microphone: int | None,
    limit: float,
    timeout: float,
    wake: WakeDetector | None,
    speaker: int | None = None,
) -> bytes:
    import speech_recognition as sr

    recognizer = sr.Recognizer()
    with sr.Microphone(device_index=microphone, sample_rate=16000) as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.3)
        if wake is not None:
            wake.wait(source)
        # A short acknowledgement lets a headless droid tell us when to speak.
        # Close the input while playing it, then reopen to discard the cue/echo.
    play_wav(files("iris_sounds").joinpath("bleep.wav").read_bytes(), speaker)
    with sr.Microphone(device_index=microphone, sample_rate=16000) as source:
        print("Speak now...", flush=True)
        audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=limit)
    # SpeechRecognition's chunk boundaries can run slightly over its time cap.
    pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
    return write_wav(pcm[: int(limit * 16000) * 2], 16000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True, help="Iris droid server URL, not the LLM URL")
    parser.add_argument("--text", action="store_true", help="Test transport without any audio")
    parser.add_argument("--activation", choices=["enter", "continuous", "wake"], default="enter")
    parser.add_argument("--wake-model", type=Path)
    parser.add_argument("--wake-phrase", default="hey iris")
    parser.add_argument("--microphone", type=int)
    parser.add_argument("--speaker", type=int, help="sounddevice output device index")
    parser.add_argument("--phrase-time-limit", type=float, default=30)
    parser.add_argument("--listen-timeout", type=float, default=10)
    args = parser.parse_args(argv)
    try:
        if not 0 < args.phrase_time_limit <= 300 or not 0 < args.listen_timeout <= 300:
            raise ValueError("Recording limit and listen timeout must be in (0, 300]")
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
                        )
                    except sr.WaitTimeoutError:
                        print("Didn't hear speech. Try again.")
                        continue
                try:
                    reply = client.turn(body, text_only=args.text)
                except httpx.HTTPError as error:
                    status = (
                        str(error.response.status_code)
                        if isinstance(error, httpx.HTTPStatusError)
                        else type(error).__name__
                    )
                    print(
                        f"Droid connection/turn failed ({status}). Check the PC; turn not resent."
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
