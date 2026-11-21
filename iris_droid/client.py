"""Droid-side microphone, optional local wake detection, and speaker playback."""

from __future__ import annotations

import base64
import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from functools import partial
from importlib.resources import files
from threading import Event
from time import monotonic as monotonic
from time import time_ns as time_ns
from typing import TYPE_CHECKING, TypedDict

import httpx

from iris_droid.capture import (
    DEFAULT_MICROPHONE_BUFFER,
    CaptureCalibration,
    CaptureInterrupted,
    CheckedMicrophoneStream,
    validate_capture_options,
)
from iris_droid.client_settings import ClientOptions, parse_client_args
from iris_droid.companion import CompanionTimer, ConsoleInput
from iris_droid.conversation import WakeConversation
from iris_droid.face_display import FaceDisplay
from iris_droid.feedback import ClientFeedback
from iris_droid.playback import (
    AudioAnimation,
    AudioClock,
    OutputStatus,
    PlaybackTrace,
    output_delay,
    play_stream,
    play_timed,
    play_wav,
    report_stream_timing,
    report_voice_timing,
    resolve_audio_name,
    resolve_microphone,
    resolve_speaker,
    scheduled_output,
    stop_playback,
)
from iris_droid.protocol import (
    TurnCancelled,
    VoiceStreamStart,
    is_clear_context,
    is_delete_previous,
    is_stop_listening,
    read_wav,
    write_wav,
)
from iris_droid.touch_commands import TouchCommands
from iris_droid.transport import DroidClient
from iris_droid.turn_state import ClientTurnState, TurnOrigin, TurnPhase
from iris_droid.volume import make_volume_backend
from iris_droid.wake import WakeDetector
from iris_turn import configure_diagnostics

if TYPE_CHECKING:
    import speech_recognition as sr

    from iris_droid.vad import SpeechDetector

__all__ = [
    "AudioAnimation",
    "AudioClock",
    "CaptureCalibration",
    "CaptureInterrupted",
    "CaptureOptions",
    "CheckedMicrophoneStream",
    "ClientFeedback",
    "ClientTurnState",
    "CompanionTimer",
    "ConsoleInput",
    "DroidClient",
    "OutputStatus",
    "PlaybackTrace",
    "TurnOrigin",
    "TurnPhase",
    "WakeConversation",
    "WakeDetector",
    "main",
    "monotonic",
    "open_microphone",
    "output_delay",
    "play_stream",
    "play_timed",
    "play_wav",
    "record_turn",
    "report_stream_timing",
    "report_voice_timing",
    "resolve_audio_name",
    "resolve_microphone",
    "resolve_speaker",
    "scheduled_output",
    "stop_playback",
    "time_ns",
    "write_wav",
]


class CaptureOptions(TypedDict, total=False):
    vad: SpeechDetector
    vad_threshold: float
    on_listening: Callable[[], None]
    interrupt: Event


@contextmanager
def open_microphone(
    device: int | None,
    buffer_frames: int = DEFAULT_MICROPHONE_BUFFER,
    sample_rate: int | None = None,
    *,
    interrupt: Event | None = None,
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
        source.stream = CheckedMicrophoneStream(source.stream, interrupt)
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
    on_listening: Callable[[], None] | None = None,
    interrupt: Event | None = None,
) -> bytes:
    import speech_recognition as sr

    validate_capture_options(
        buffer_frames=buffer_frames,
        pause_threshold=pause_threshold,
        sample_rate=sample_rate,
        energy_threshold=energy_threshold,
        calibration_duration=calibration_duration,
    )

    def source_context() -> AbstractContextManager[sr.Microphone]:
        if interrupt is None:
            return open_microphone(microphone, buffer_frames, sample_rate)
        return open_microphone(microphone, buffer_frames, sample_rate, interrupt=interrupt)

    recognizer = sr.Recognizer()
    recognizer.pause_threshold = pause_threshold
    threshold = energy_threshold
    if threshold is None and calibration is not None and wake is None:
        threshold = calibration.energy_threshold
    if threshold is None and vad is None:
        with source_context() as source:
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
        with source_context() as source:
            wake.wait(source)
        if conversation is not None:
            conversation.activate()
    if on_listening is not None and (wake is not None or not quiet):
        on_listening()
        on_listening = None
    if feedback is None and (not quiet or wake is not None):
        play_wav(files("iris_sounds").joinpath("bleep.wav").read_bytes(), speaker)
    elif feedback is not None and (not quiet or wake is not None):
        feedback.listening()
    with source_context() as source:
        if diagnostics:
            print(
                f"Microphone: {source.SAMPLE_RATE} Hz, mono, {source.CHUNK} frames "
                f"({source.CHUNK / source.SAMPLE_RATE * 1000:.0f} ms); "
                f"speech threshold: {vad_threshold if vad else recognizer.energy_threshold}; "
                f"end pause: {pause_threshold:g} s.",
                flush=True,
            )
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
                on_speech=on_listening,
                trim_silence=True,
            )
        else:
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=limit)
            pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
    # SpeechRecognition's chunk boundaries can run slightly over its time cap.
    if feedback is not None and not quiet:
        feedback.recorded()
    return write_wav(pcm[: int(limit * 16000) * 2], 16000)


def main(argv: list[str] | None = None) -> int:
    configure_diagnostics()
    try:
        startup = parse_client_args(argv)
        if startup.download_vad_model is not None:
            from iris_droid.vad import download_model

            download_model(startup.download_vad_model)
            print(f"Silero model saved to {startup.download_vad_model}")
            return 0
        if startup.list_microphones:
            import speech_recognition as sr

            for index, name in enumerate(sr.Microphone.list_microphone_names()):
                print(f"{index}: {name}")
            return 0
        args = ClientOptions.from_namespace(startup)
        wake_window = WakeConversation(args.conversation_timeout)
        companion = CompanionTimer(args.companion_idle_min, args.companion_idle_max)
        console = ConsoleInput()
        if args.play_wav is not None and (args.text or args.test_microphone):
            raise ValueError("--play-wav cannot be combined with --text or --test-microphone")
        if args.test_microphone and args.text:
            raise ValueError("--test-microphone cannot be combined with --text")
        if args.voice_cancel and (args.text or args.test_microphone or args.play_wav is not None):
            raise ValueError("--voice-cancel is for normal microphone turns")
        if args.voice_cancel and args.wake_model is None:
            raise ValueError("--voice-cancel requires --wake-model with a local Vosk model folder")
        if not args.text:
            if isinstance(args.speaker, str):
                name = args.speaker
                args.speaker = resolve_speaker(name)
                print(f"Speaker {name!r} resolved to index {args.speaker}.")
            if args.play_wav is None and isinstance(args.microphone, str):
                name = args.microphone
                args.microphone = resolve_microphone(name)
                print(f"Microphone {name!r} resolved to index {args.microphone}.")
        # Device names have been resolved for audio modes. Text mode needs no device.
        speaker = args.speaker if isinstance(args.speaker, int) else None
        microphone = args.microphone if isinstance(args.microphone, int) else None
        if args.play_wav is not None:
            play_wav(
                args.play_wav.read_bytes(),
                speaker,
                lead_in=args.speech_lead_in,
                primer=args.speech_primer,
            )
            return 0
        vad = None
        if args.vad_model is not None and not args.text:
            from iris_droid.vad import SileroVAD

            vad = SileroVAD(args.vad_model)
            print(f"Local speech detection: Silero VAD (threshold {args.vad_threshold:g}).")
        local_capture_options: CaptureOptions = {}
        if vad is not None:
            local_capture_options = {"vad": vad, "vad_threshold": args.vad_threshold}
        with ClientFeedback(
            speaker, play_wav, stop_playback, enabled=not args.no_sounds and not args.text
        ) as feedback:
            if args.test_microphone:
                print("Local microphone test. Stay quiet until the listening cue, then speak.")
                import speech_recognition as sr

                try:
                    body = record_turn(
                        microphone,
                        args.phrase_time_limit,
                        args.listen_timeout,
                        None,
                        speaker,
                        args.microphone_buffer,
                        args.pause_threshold,
                        sample_rate=args.microphone_rate,
                        energy_threshold=args.energy_threshold,
                        calibration_duration=args.calibration_duration,
                        diagnostics=True,
                        feedback=feedback,
                        **local_capture_options,
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
                play_wav(body, speaker)
                return 0
            display = FaceDisplay(args.face, args.face_idle, remember=True)
            face_audio: AudioAnimation = {}
            if display.head is not None:
                face_audio["on_audio"] = display.speech.feed
            with (
                httpx.Client(
                    trust_env=False,
                    follow_redirects=False,
                    # Let the server report its own timeout even at the one-hour turn limit.
                    timeout=httpx.Timeout(600, connect=10, read=3610),
                ) as transport,
                ExitStack() as stack,
            ):

                def show_listening() -> None:
                    display.show("listening")

                capture_options: CaptureOptions = {}
                if vad is not None:
                    capture_options["vad"] = vad
                    capture_options["vad_threshold"] = args.vad_threshold
                if args.face is not None:
                    capture_options["on_listening"] = show_listening

                def stream_player(start: VoiceStreamStart, chunks: Iterator[bytes]) -> int | None:
                    turn_state.begin_speaking()
                    display.clear_notice()
                    with display.speaking(start.reply.expression):
                        return play_stream(
                            chunks,
                            start.rate,
                            speaker,
                            lead_in=args.speech_lead_in,
                            primer=args.speech_primer,
                            before_output=feedback.stop_working,
                            **face_audio,
                        )

                detector = None
                if not args.text and (args.activation == "wake" or args.voice_cancel):
                    if args.wake_model is None:
                        raise ValueError(
                            "Wake mode requires --wake-model with a local Vosk model folder"
                        )
                    detector = WakeDetector(args.wake_model, args.wake_phrase)
                wake = detector if args.activation == "wake" else None

                def listen_for_cancel(stop: Event) -> bool:
                    assert detector is not None
                    with open_microphone(
                        microphone, args.microphone_buffer, args.microphone_rate
                    ) as source:
                        return detector.listen_for_cancel(source, stop)

                stream_replies = not (args.text or args.no_speech_stream or args.save_reply)
                client = DroidClient(
                    args.server,
                    os.environ.get("IRIS_DROID_TOKEN", ""),
                    transport,
                    wake_conversation=args.activation == "wake" and not args.text,
                    trace_voice=args.trace_voice,
                    stream_player=stream_player if stream_replies else None,
                    face=args.face is not None,
                    cancel_listener=listen_for_cancel if args.voice_cancel else None,
                    cancel_feedback=feedback if args.voice_cancel else None,
                    personalities_file=args.personalities_file,
                    volume_backend=make_volume_backend(
                        args.volume_backend,
                        card=args.alsa_card,
                        control=args.alsa_control,
                        remember=True,
                    ),
                )
                stack.callback(client.state.close)
                touch = TouchCommands()
                stack.callback(touch.close)
                if args.face is not None:
                    display.execute_command = touch.submit
                    display.commands_ready = lambda: touch.ready
                    capture_options["interrupt"] = touch.interrupt
                display.personalities = tuple(client.personality_headers)
                display.active_personality = lambda: client.personality
                display.select_personality = client.select_personality
                if wake is not None and not args.text:
                    display.wake_phrase = args.wake_phrase
                face_port = stack.enter_context(display.serve(args.face_port))
                if face_port is not None:
                    print(
                        f"Iris face: open http://127.0.0.1:{face_port}/ on this display.",
                        flush=True,
                    )
                conversation = wake_window if wake is not None else None
                calibration = (
                    CaptureCalibration() if vad is None and args.energy_threshold is None else None
                )
                companion_calibration = CaptureCalibration()
                first_capture = True
                turn_state = ClientTurnState()
                print("Iris droid ready. Say/type exit to finish; Ctrl-C also stops the client.")
                while True:
                    touch.listen()
                    tapped = touch.take()
                    turn_state.finish_turn(
                        wake_active=conversation is not None and conversation.active
                    )
                    if not args.text:
                        display.show("idle")
                    if (
                        conversation is not None
                        and not companion.enabled
                        and conversation.active
                        and conversation.remaining() <= 0
                    ):
                        conversation.sleep()
                        turn_state.finish_turn(wake_active=False)
                        print("Conversation idle; returning to wake waiting.", flush=True)
                    origin: TurnOrigin = (
                        "touch" if tapped is not None else ("text" if args.text else "voice")
                    )
                    if (
                        tapped is None
                        and not args.text
                        and args.activation == "enter"
                        and not companion.enabled
                    ):
                        feedback.ready()
                    if tapped is not None:
                        body = tapped.encode()
                    elif args.text:
                        turn_state.begin_listening("text")
                        try:
                            line = console.read(
                                "You: ",
                                companion.remaining() if companion.enabled else None,
                                **({"interrupt": touch.interrupt} if args.face is not None else {}),
                            )
                        except CaptureInterrupted:
                            continue
                        if line is None:
                            origin = "proactive"
                        body = b"idle" if origin == "proactive" else (line or "").encode()
                        if origin != "proactive" and not body.strip():
                            continue
                    else:
                        import speech_recognition as sr

                        if args.activation == "enter" and not companion.enabled:
                            if args.face is None:
                                input("Press Enter to talk...")
                            else:
                                try:
                                    console.read(
                                        "Press Enter to talk...", interrupt=touch.interrupt
                                    )
                                except CaptureInterrupted:
                                    continue
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
                        turn_state.begin_listening("voice")
                        try:
                            body = record_turn(
                                microphone,
                                args.phrase_time_limit,
                                args.listen_timeout,
                                wake
                                if not companion.enabled
                                and (conversation is None or not conversation.active)
                                else None,
                                speaker,
                                args.microphone_buffer,
                                args.pause_threshold,
                                sample_rate=args.microphone_rate,
                                energy_threshold=args.energy_threshold,
                                calibration_duration=args.calibration_duration,
                                diagnostics=args.echo_recording,
                                feedback=feedback,
                                announce_ready=announce_ready,
                                quiet=quiet,
                                **capture_options,
                                conversation=None if companion.enabled else conversation,
                                calibration=companion_calibration
                                if companion.enabled
                                else calibration,
                                companion_timer=companion if companion.enabled else None,
                            )
                        except CaptureInterrupted:
                            continue
                        except sr.WaitTimeoutError:
                            if companion.enabled:
                                origin = "proactive"
                                body = b"idle"
                            elif conversation is not None and conversation.active:
                                conversation.sleep()
                                turn_state.finish_turn(wake_active=False)
                                print("Conversation idle; returning to wake waiting.", flush=True)
                                continue
                            else:
                                if not quiet:
                                    feedback.error()
                                    print("Didn't hear speech. Try again.")
                                continue
                        if conversation is not None:
                            conversation.activate()
                        if args.echo_recording and origin != "proactive":
                            pcm, rate = read_wav(body, input_audio=True)
                            print(
                                f"Recorded {len(pcm) / (rate * 2):.1f} seconds. "
                                "Playing recording before sending...",
                                flush=True,
                            )
                            play_wav(body, speaker)
                    if origin != "touch" and not touch.begin_voice():
                        continue
                    proactive = origin == "proactive"
                    turn_state.begin_processing(origin)
                    try:
                        display.show("thinking")
                        if not proactive:
                            display.notify(
                                "Processing…",
                                "Applying your command."
                                if origin == "touch"
                                else "Checking what you said.",
                                "processing",
                                seconds=600,
                            )
                        with (
                            nullcontext()
                            if proactive or (args.voice_cancel and origin == "voice")
                            else feedback.working()
                        ):
                            reply = client.turn(
                                body,
                                text_only=args.text or origin == "touch",
                                **({"companion_idle": True} if proactive else {}),
                            )
                    except TurnCancelled:
                        display.show("idle")
                        display.notify("Cancelled", "This turn was discarded.")
                        print("Voice turn discarded.", flush=True)
                        if conversation is not None and conversation.active:
                            conversation.activate()
                        continue
                    except httpx.HTTPError as error:
                        display.show("idle")
                        display.notify(
                            "No confirmation received",
                            "Check the command's status before trying again.",
                            "error",
                        )
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
                    display.companion_mode = reply.companion_mode
                    if reply.command is not None:
                        display.notify(
                            reply.command.title, reply.command.message, reply.command.status
                        )
                    else:
                        display.clear_notice()
                    private = client.private_mode is True
                    if private and not args.show_private_text:
                        print("Private conversation: transcript hidden.")
                    else:
                        print(
                            f"Iris: {reply.text}"
                            if proactive
                            else f"You: {reply.heard}\nIris: {reply.text}"
                        )
                    stop_listening = conversation is not None and is_stop_listening(reply.heard)
                    if (
                        not reply.audio
                        and (
                            is_clear_context(reply.heard)
                            or is_delete_previous(reply.heard)
                            or stop_listening
                        )
                        and not getattr(reply, "_streamed", False)
                    ):
                        feedback.acknowledge()
                    if reply.audio and not args.text:
                        reply_audio = base64.b64decode(reply.audio, validate=True)
                        read_wav(reply_audio)
                        if args.save_reply is not None and not private:
                            args.save_reply.parent.mkdir(parents=True, exist_ok=True)
                            args.save_reply.write_bytes(reply_audio)
                            print(f"Saved unmodified reply audio to {args.save_reply}", flush=True)
                        playback_trace: PlaybackTrace = {}
                        if args.trace_voice:
                            if reply._voice_timing is None:
                                print(
                                    "Voice timing unavailable: the server sent no timing.",
                                    flush=True,
                                )
                            else:
                                playback_trace["on_speech_start"] = partial(
                                    report_voice_timing, reply._voice_timing
                                )
                        turn_state.begin_speaking()
                        cue = reply.expression if face_port is not None else None
                        with display.speaking(cue):
                            play_wav(
                                reply_audio,
                                speaker,
                                lead_in=args.speech_lead_in,
                                primer=args.speech_primer,
                                **face_audio,
                                **playback_trace,
                            )
                    elif face_port is not None and args.text and reply.text:
                        display.show("listening", reply.expression, seconds=3)
                    if stop_listening and conversation is not None:
                        companion.update(False)
                        conversation.sleep()
                        turn_state.finish_turn(wake_active=False)
                        print(f'Waiting for "{args.wake_phrase}" again.', flush=True)
                        continue
                    if reply.finished:
                        if face_port is not None:
                            # Keep the local display server alive long enough to show
                            # the final acknowledgement before shutting down.
                            display.show("idle")
                            display.notify("Conversation ended", "Iris is stopping.", "success")
                            Event().wait(3)
                        return 0
                    companion.update(reply.companion_mode, proactive=proactive)
                    if conversation is not None and turn_state.should_activate_wake(
                        was_wake_active=conversation.active
                    ):
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
