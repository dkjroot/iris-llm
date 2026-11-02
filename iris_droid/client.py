"""Droid-side microphone, optional local wake detection, and speaker playback."""

from __future__ import annotations

import argparse
import base64
import os
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from iris_droid.capture import (
    DEFAULT_MICROPHONE_BUFFER,
    CaptureCalibration,
    CheckedMicrophoneStream,
    validate_capture_options,
)
from iris_droid.companion import CompanionTimer, ConsoleInput
from iris_droid.conversation import WakeConversation
from iris_droid.feedback import ClientFeedback
from iris_droid.protocol import (
    MAX_BODY,
    TurnReply,
    is_clear_context,
    is_stop_listening,
    read_wav,
    validate_token,
    write_wav,
)
from iris_droid.wake import WakeDetector

if TYPE_CHECKING:
    import speech_recognition as sr

    from iris_droid.vad import SpeechDetector


class DroidClient:
    def __init__(
        self, url: str, token: str, client: httpx.Client, *, wake_conversation: bool = False
    ) -> None:
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
        if wake_conversation:
            self.headers["X-Iris-Wake-Conversation"] = "1"

    def turn(
        self, body: bytes, *, text_only: bool = False, companion_idle: bool = False
    ) -> TurnReply:
        if not 0 < len(body) <= MAX_BODY:
            raise ValueError("Turn is empty or exceeds the transport size limit")
        headers = {
            **self.headers,
            "Content-Type": "text/plain" if text_only or companion_idle else "audio/wav",
        }
        if companion_idle:
            headers["X-Iris-Companion-Idle"] = "1"
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


def play_wav(
    data: bytes,
    device: int | None = None,
    *,
    loop: bool = False,
    lead_in: float = 0,
    primer: bool = False,
) -> None:
    if not 0 <= lead_in <= 2:
        raise ValueError("Speech lead-in must be between 0 and 2 seconds")
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
        prefixes = []
        if primer:
            cue, cue_rate = read_wav(files("iris_sounds").joinpath("chirp.wav").read_bytes())
            cue = sr.AudioData(cue, cue_rate, 2).get_raw_data(convert_rate=rate)
            prefixes.append(np.frombuffer(cue, dtype="<i2"))
        if lead_in:
            prefixes.append(np.zeros(round(rate * lead_in), dtype="<i2"))
        if prefixes:
            samples = np.concatenate([*prefixes, samples])
        if loop:
            sd.play(samples, rate, device=device, blocking=False, loop=True, latency="high")
        else:
            sd.play(samples, rate, device=device, blocking=True, latency="high")
            status = sd.get_status()
            if status is not None and status.output_underflow:
                print("Speaker playback underrun: some audio may have been lost.", flush=True)
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
    conversation: WakeConversation | None = None,
    calibration: CaptureCalibration | None = None,
    companion_timer: CompanionTimer | None = None,
    vad: SpeechDetector | None = None,
    vad_threshold: float = 0.5,
    quiet: bool = False,
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
    threshold = energy_threshold
    if threshold is None and calibration is not None and wake is None:
        threshold = calibration.energy_threshold
    if threshold is None and vad is None:
        with open_microphone(microphone, buffer_frames, sample_rate) as source:
            # Always read at least one buffer, even at low device rates or with
            # a large buffer. The library otherwise calibrates using no audio.
            duration = max(calibration_duration, source.CHUNK / source.SAMPLE_RATE * 1.01)
            recognizer.adjust_for_ambient_noise(source, duration=duration)
        if calibration is not None:
            calibration.energy_threshold = recognizer.energy_threshold
    elif threshold is not None:
        recognizer.energy_threshold = threshold
    # Some SpeechRecognition versions also adapt during the phrase. Steady
    # speech then raises the threshold above the voice and ends capture early.
    recognizer.dynamic_energy_threshold = False
    # Ready means calibration is finished. In wake mode the next capture waits
    # for the wake phrase; cue playback never shares an open microphone.
    if announce_ready and feedback is not None:
        feedback.ready()
    if wake is not None:
        with open_microphone(microphone, buffer_frames, sample_rate) as source:
            wake.wait(source)
        if conversation is not None:
            conversation.activate()
    if feedback is None and (not quiet or wake is not None):
        play_wav(files("iris_sounds").joinpath("bleep.wav").read_bytes(), speaker)
    elif feedback is not None and (not quiet or wake is not None):
        feedback.listening()
    with open_microphone(microphone, buffer_frames, sample_rate) as source:
        if diagnostics:
            print(
                f"Microphone: {source.SAMPLE_RATE} Hz, mono, {source.CHUNK} frames "
                f"({source.CHUNK / source.SAMPLE_RATE * 1000:.0f} ms); "
                f"speech threshold: {vad_threshold if vad else recognizer.energy_threshold}; "
                f"end pause: {pause_threshold:g} s.",
                flush=True,
            )
        print("Speak now...", flush=True)
        if companion_timer is not None and companion_timer.enabled:
            timeout = companion_timer.remaining()
            if timeout <= 0:
                raise sr.WaitTimeoutError("Companion idle timeout")
        elif conversation is not None and conversation.active:
            timeout = conversation.remaining()
            if timeout <= 0:
                raise sr.WaitTimeoutError("Conversation idle timeout")
        if vad is not None:
            from iris_droid.vad import listen_for_speech

            pcm = listen_for_speech(
                source,
                vad,
                timeout=timeout,
                limit=limit,
                pause=pause_threshold,
                threshold=vad_threshold,
            )
        else:
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=limit)
            pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
    # SpeechRecognition's chunk boundaries can run slightly over its time cap.
    if feedback is not None and not quiet:
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
    parser.add_argument("--vad-model", type=Path, help="Enable local Silero speech detection")
    parser.add_argument(
        "--download-vad-model",
        type=Path,
        metavar="PATH",
        help="Download the pinned Silero model here and exit (no microphone or server)",
    )
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
        "--microphone-buffer",
        type=int,
        default=DEFAULT_MICROPHONE_BUFFER,
        help="Capture buffer in frames (256–65536; default: 8192). Increase for input overflow.",
    )
    parser.add_argument("--phrase-time-limit", type=float, default=30)
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
    args = parser.parse_args(argv)
    try:
        if args.download_vad_model is not None:
            from iris_droid.vad import download_model

            download_model(args.download_vad_model)
            print(f"Silero model saved to {args.download_vad_model}")
            return 0
        if args.list_microphones:
            import speech_recognition as sr

            for index, name in enumerate(sr.Microphone.list_microphone_names()):
                print(f"{index}: {name}")
            return 0
        if not 0 < args.phrase_time_limit <= 300 or not 0 < args.listen_timeout <= 300:
            raise ValueError("Recording limit and listen timeout must be in (0, 300]")
        if not 0 <= args.speech_lead_in <= 2:
            raise ValueError("Speech lead-in must be between 0 and 2 seconds")
        if not 0 < args.vad_threshold < 1:
            raise ValueError("--vad-threshold must be between 0 and 1, exclusive")
        wake_window = WakeConversation(args.conversation_timeout)
        companion = CompanionTimer(args.companion_idle_min, args.companion_idle_max)
        console = ConsoleInput()
        validate_capture_options(
            buffer_frames=args.microphone_buffer,
            pause_threshold=args.pause_threshold,
            sample_rate=args.microphone_rate,
            energy_threshold=args.energy_threshold,
            calibration_duration=args.calibration_duration,
        )
        if args.play_wav is not None:
            if args.text or args.test_microphone:
                raise ValueError("--play-wav cannot be combined with --text or --test-microphone")
            play_wav(
                args.play_wav.read_bytes(),
                args.speaker,
                lead_in=args.speech_lead_in,
                primer=args.speech_primer,
            )
            return 0
        vad = None
        if args.vad_model is not None and not args.text:
            from iris_droid.vad import SileroVAD

            vad = SileroVAD(args.vad_model)
            print(f"Local speech detection: Silero VAD (threshold {args.vad_threshold:g}).")
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
                        **({"vad": vad, "vad_threshold": args.vad_threshold} if vad else {}),
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
                client = DroidClient(
                    args.server,
                    os.environ.get("IRIS_DROID_TOKEN", ""),
                    transport,
                    wake_conversation=args.activation == "wake" and not args.text,
                )
                wake = None
                if not args.text and args.activation == "wake":
                    if args.wake_model is None:
                        raise ValueError(
                            "Wake mode requires --wake-model with a local Vosk model folder"
                        )
                    wake = WakeDetector(args.wake_model, args.wake_phrase)
                conversation = wake_window if wake is not None else None
                calibration = CaptureCalibration() if conversation is not None else None
                companion_calibration = CaptureCalibration()
                first_capture = True
                print("Iris droid ready. Say/type exit to finish; Ctrl-C also stops the client.")
                while True:
                    if (
                        conversation is not None
                        and not companion.enabled
                        and conversation.active
                        and conversation.remaining() <= 0
                    ):
                        conversation.sleep()
                        print("Conversation idle; returning to wake waiting.", flush=True)
                    proactive = False
                    if not args.text and args.activation == "enter" and not companion.enabled:
                        feedback.ready()
                    if args.text:
                        line = console.read(
                            "You: ", companion.remaining() if companion.enabled else None
                        )
                        proactive = line is None
                        body = b"idle" if proactive else (line or "").encode()
                        if not proactive and not body.strip():
                            continue
                    else:
                        import speech_recognition as sr

                        if args.activation == "enter" and not companion.enabled:
                            input("Press Enter to talk...")
                        quiet = args.activation != "enter" or companion.enabled
                        announce_ready = quiet and (
                            first_capture
                            or (
                                wake is not None
                                and not companion.enabled
                                and conversation is not None
                                and not conversation.active
                            )
                        )
                        first_capture = False
                        try:
                            body = record_turn(
                                args.microphone,
                                args.phrase_time_limit,
                                args.listen_timeout,
                                wake
                                if not companion.enabled
                                and (conversation is None or not conversation.active)
                                else None,
                                args.speaker,
                                args.microphone_buffer,
                                args.pause_threshold,
                                sample_rate=args.microphone_rate,
                                energy_threshold=args.energy_threshold,
                                calibration_duration=args.calibration_duration,
                                diagnostics=args.echo_recording,
                                feedback=feedback,
                                announce_ready=announce_ready,
                                quiet=quiet,
                                **(
                                    {"vad": vad, "vad_threshold": args.vad_threshold} if vad else {}
                                ),
                                conversation=None if companion.enabled else conversation,
                                calibration=companion_calibration
                                if companion.enabled
                                else calibration,
                                companion_timer=companion if companion.enabled else None,
                            )
                        except sr.WaitTimeoutError:
                            if companion.enabled:
                                proactive = True
                                body = b"idle"
                            elif conversation is not None and conversation.active:
                                conversation.sleep()
                                print("Conversation idle; returning to wake waiting.", flush=True)
                                continue
                            else:
                                if not quiet:
                                    feedback.error()
                                    print("Didn't hear speech. Try again.")
                                continue
                        if conversation is not None:
                            conversation.activate()
                        if args.echo_recording and not proactive:
                            pcm, rate = read_wav(body, input_audio=True)
                            print(
                                f"Recorded {len(pcm) / (rate * 2):.1f} seconds. "
                                "Playing recording before sending...",
                                flush=True,
                            )
                            play_wav(body, args.speaker)
                    try:
                        with nullcontext() if proactive else feedback.working():
                            reply = client.turn(
                                body,
                                text_only=args.text,
                                **({"companion_idle": True} if proactive else {}),
                            )
                    except httpx.HTTPError as error:
                        # Failed input hasn't confirmed intelligible speech. Keep
                        # its deadline and unanswered-opener backoff intact, but
                        # pace failed proactive requests so they don't spin.
                        if companion.enabled and proactive:
                            companion.update(True, proactive=True)
                        if conversation is not None and conversation.active:
                            conversation.activate()
                        unrecognized = (
                            isinstance(error, httpx.HTTPStatusError)
                            and error.response.status_code == 422
                        )
                        if not proactive and not (unrecognized and not args.text and quiet):
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
                    print(
                        f"Iris: {reply.text}"
                        if proactive
                        else f"You: {reply.heard}\nIris: {reply.text}"
                    )
                    if not reply.audio and (
                        is_clear_context(reply.heard)
                        or (conversation is not None and is_stop_listening(reply.heard))
                    ):
                        feedback.acknowledge()
                    if conversation is not None and is_stop_listening(reply.heard):
                        companion.update(False)
                        conversation.sleep()
                        print(f'Waiting for "{args.wake_phrase}" again.', flush=True)
                        continue
                    if reply.audio and not args.text:
                        reply_audio = base64.b64decode(reply.audio, validate=True)
                        read_wav(reply_audio)
                        if args.save_reply is not None:
                            args.save_reply.parent.mkdir(parents=True, exist_ok=True)
                            args.save_reply.write_bytes(reply_audio)
                            print(f"Saved unmodified reply audio to {args.save_reply}", flush=True)
                        play_wav(
                            reply_audio,
                            args.speaker,
                            lead_in=args.speech_lead_in,
                            primer=args.speech_primer,
                        )
                    if reply.finished:
                        return 0
                    companion.update(reply.companion_mode, proactive=proactive)
                    if conversation is not None:
                        # Give the user a full idle interval after the reply finishes.
                        conversation.activate()
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
