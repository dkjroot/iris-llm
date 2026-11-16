"""Droid-side microphone, optional local wake detection, and speaker playback."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, nullcontext
from functools import partial
from importlib.resources import files
from math import isfinite
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event
from time import time_ns
from typing import TYPE_CHECKING, Protocol, TypedDict
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError

from iris_droid.capture import (
    DEFAULT_MICROPHONE_BUFFER,
    CaptureCalibration,
    CheckedMicrophoneStream,
    validate_capture_options,
)
from iris_droid.companion import CompanionTimer, ConsoleInput
from iris_droid.conversation import WakeConversation
from iris_droid.expression import FACE_HEADER
from iris_droid.face_display import DEFAULT_FACE_PORT, FACE_HEADS, FACE_IDLE_MODES, FaceDisplay
from iris_droid.feedback import ClientFeedback
from iris_droid.protocol import (
    MAX_BODY,
    VOICE_STREAM_TYPE,
    VOICE_TIMING_HEADER,
    TurnReply,
    VoiceStreamStart,
    VoiceTiming,
    is_clear_context,
    is_stop_listening,
    read_wav,
    validate_token,
    write_wav,
)
from iris_droid.volume import VolumeBackend, execute_volume, make_volume_backend, volume_command
from iris_droid.wake import WakeDetector

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt
    import speech_recognition as sr

    from iris_droid.vad import SpeechDetector


class CaptureOptions(TypedDict, total=False):
    vad: SpeechDetector
    vad_threshold: float
    on_listening: Callable[[], None]


class DroidClient:
    def __init__(
        self,
        url: str,
        token: str,
        client: httpx.Client,
        *,
        wake_conversation: bool = False,
        volume_backend: VolumeBackend | None = None,
        trace_voice: bool = False,
        stream_player: Callable[[VoiceStreamStart, Iterator[bytes]], int | None] | None = None,
        face: bool = False,
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
        self.private_mode = False
        self.url = url.rstrip("/")
        self.client = client
        self.volume_backend = volume_backend
        self.trace_voice = trace_voice
        self.stream_player = stream_player
        self.headers = {"Authorization": f"Bearer {token}", "X-Iris-Session": str(uuid4())}
        self.headers["X-Iris-Volume"] = "1"
        if wake_conversation:
            self.headers["X-Iris-Wake-Conversation"] = "1"
        if face:
            self.headers[FACE_HEADER] = "1"

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
        reply = self._request(route, body, headers)
        if reply.volume is not None:
            if companion_idle or reply.volume != volume_command(reply.heard):
                raise ValueError("Unexpected speaker volume action")
            result = execute_volume(self.volume_backend, reply.volume)
            # Report the actual read-back, never an assumed success. This second
            # request only synthesizes a fixed acknowledgement; it cannot act.
            heard = reply.heard
            print(result.description(), flush=True)
            reply = self._request(
                "volume-result",
                result.model_dump_json().encode(),
                {
                    **self.headers,
                    "Content-Type": "application/json",
                    "X-Iris-Text-Only": "1" if text_only else "0",
                },
            )
            if reply.volume is not None:
                raise ValueError("Unexpected action in volume acknowledgement")
            reply.heard = heard
        return reply

    def _request(self, route: str, body: bytes, headers: dict[str, str]) -> TurnReply:
        if self.trace_voice:
            headers = {**headers, VOICE_TIMING_HEADER: "1"}
        if route != "text" and self.stream_player is not None:
            headers = {**headers, "Accept": VOICE_STREAM_TYPE}
        with self.client.stream(
            "POST", f"{self.url}/v1/{route}", content=body, headers=headers
        ) as response:
            response.raise_for_status()
            private = response.headers.get("X-Iris-Private")
            if private in {"0", "1"}:
                self.private_mode = private == "1"
            if response.headers.get("Content-Type") == VOICE_STREAM_TYPE:
                return self._stream_reply(response)
            timing = response.headers.get(VOICE_TIMING_HEADER)
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise ValueError("Server response exceeds the transport size limit")
        reply = TurnReply.model_validate_json(bytes(data))
        if self.trace_voice and timing is not None:
            try:
                reply._voice_timing = VoiceTiming.model_validate_json(timing)
            except ValidationError:
                print("Voice timing unavailable: invalid server timing.", flush=True)
        return reply

    def _stream_reply(self, response: httpx.Response) -> TurnReply:
        lines = response.iter_lines()
        timing: VoiceTiming | None = None

        def chunks() -> Iterator[bytes]:
            nonlocal timing
            size = 0
            for line in lines:
                if len(line) > MAX_BODY:
                    raise ValueError("Oversized voice stream frame")
                frame = json.loads(line)
                if not isinstance(frame, dict):
                    raise ValueError("Invalid voice stream frame")
                event = frame.get("event")
                if event == "done":
                    timing = VoiceTiming.model_validate(frame.get("timing"))
                    return
                if event != "audio" or not isinstance(frame.get("pcm"), str):
                    raise ValueError("Voice stream stopped before the reply finished")
                pcm = base64.b64decode(frame["pcm"], validate=True)
                size += len(pcm)
                if not pcm or len(pcm) % 2 or size > MAX_BODY:
                    raise ValueError("Invalid voice stream audio")
                yield pcm
            raise ValueError("Voice stream ended without a completion frame")

        try:
            if self.stream_player is None:
                raise ValueError("Unexpected voice stream")
            first = next(lines, "")
            if len(first) > MAX_BODY:
                raise ValueError("Oversized voice stream header")
            start = VoiceStreamStart.model_validate_json(first)
            scheduled_ns = self.stream_player(start, chunks())
            completed = timing
            if completed is None:
                raise ValueError("Incomplete voice stream")
        except ValueError as error:
            raise httpx.RemoteProtocolError(str(error), request=response.request) from error
        if self.trace_voice:
            report_stream_timing(completed, start.first_audio_ns, scheduled_ns)
        start.reply._streamed = True
        return start.reply


def play_wav(
    data: bytes,
    device: int | None = None,
    *,
    loop: bool = False,
    lead_in: float = 0,
    primer: bool = False,
    on_speech_start: Callable[[int | None], None] | None = None,
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
        if on_speech_start is not None and not loop:
            # Ignore the primer, padding and exact silence in the synthesized WAV.
            first = int(np.argmax(samples != 0)) if samples.any() else None
            speech_frame = (
                None if first is None else sum(len(prefix) for prefix in prefixes) + first
            )
        else:
            speech_frame = None
        if prefixes:
            samples = np.concatenate([*prefixes, samples])
        if loop:
            sd.play(samples, rate, device=device, blocking=False, loop=True, latency="high")
        elif on_speech_start is not None:
            play_timed(samples, rate, device, speech_frame, on_speech_start)
        else:
            sd.play(samples, rate, device=device, blocking=True, latency="high")
            status = sd.get_status()
            if status is not None and status.output_underflow:
                print("Speaker playback underrun: some audio may have been lost.", flush=True)
    except sd.PortAudioError as error:
        raise OSError(f"Speaker {device if device is not None else 'default'}: {error}") from error


class AudioClock(Protocol):
    @property
    def currentTime(self) -> float: ...

    @property
    def outputBufferDacTime(self) -> float: ...


class OutputStatus(Protocol):
    @property
    def output_underflow(self) -> bool: ...


class PlaybackTrace(TypedDict, total=False):
    on_speech_start: Callable[[int | None], None]


def scheduled_output(timing: AudioClock, frame: int, rate: int) -> int | None:
    callback_ns = time_ns()
    current = timing.currentTime
    dac = timing.outputBufferDacTime
    if isfinite(current) and isfinite(dac) and dac >= current and dac > 0:
        return callback_ns + round((dac - current + frame / rate) * 1e9)
    return None


def play_timed(
    samples: npt.NDArray[np.int16],
    rate: int,
    device: int | None,
    speech_frame: int | None,
    on_speech_start: Callable[[int | None], None],
) -> None:
    import sounddevice as sd

    finished = Event()
    position = 0
    scheduled_ns: int | None = None
    underrun = False

    def callback(
        outdata: npt.NDArray[np.int16], frames: int, timing: AudioClock, status: OutputStatus
    ) -> None:
        nonlocal position, scheduled_ns, underrun
        underrun |= status.output_underflow
        if speech_frame is not None and position <= speech_frame < position + frames:
            scheduled_ns = scheduled_output(timing, speech_frame - position, rate)
        count = min(frames, len(samples) - position)
        outdata[:count, 0] = samples[position : position + count]
        outdata[count:].fill(0)
        position += count
        if position == len(samples):
            raise sd.CallbackStop

    with sd.OutputStream(
        samplerate=rate,
        channels=1,
        dtype="int16",
        device=device,
        latency="high",
        callback=callback,
        finished_callback=finished.set,
    ):
        finished.wait()
    on_speech_start(scheduled_ns)
    if underrun:
        print("Speaker playback underrun: some audio may have been lost.", flush=True)


def play_stream(
    chunks: Iterator[bytes], rate: int, device: int | None, *, lead_in: float, primer: bool
) -> int | None:
    import numpy as np
    import sounddevice as sd
    import speech_recognition as sr

    try:
        try:
            sd.check_output_settings(device=device, channels=1, dtype="int16", samplerate=rate)
            output_rate = rate
        except sd.PortAudioError:
            output_rate = int(sd.query_devices(device, "output")["default_samplerate"])
            sd.check_output_settings(
                device=device, channels=1, dtype="int16", samplerate=output_rate
            )
        # Buffer a small amount before opening the device to absorb network jitter.
        initial: list[bytes] = []
        size = 0
        while size < round(rate * 0.16) * 2:
            pcm = next(chunks, None)
            if pcm is None:
                break
            initial.append(pcm)
            size += len(pcm)
        if not initial:
            raise ValueError("Voice stream returned no audio")

        queue: SimpleQueue[tuple[npt.NDArray[np.int16], int | None] | None] = SimpleQueue()
        if primer:
            cue, cue_rate = read_wav(files("iris_sounds").joinpath("chirp.wav").read_bytes())
            cue = sr.AudioData(cue, cue_rate, 2).get_raw_data(convert_rate=output_rate)
            queue.put((np.frombuffer(cue, dtype="<i2"), None))
        if lead_in:
            queue.put((np.zeros(round(output_rate * lead_in), dtype="<i2"), None))

        converter = None

        def enqueue(pcm: bytes) -> None:
            nonlocal converter
            if output_rate != rate:
                pcm, converter = sr.audioop.ratecv(pcm, 2, 1, rate, output_rate, converter)
            samples = np.frombuffer(pcm, dtype="<i2")
            if samples.size:
                first = int(np.argmax(samples != 0)) if samples.any() else None
                queue.put((samples, first))

        for pcm in initial:
            enqueue(pcm)
        finished = Event()
        current: tuple[npt.NDArray[np.int16], int | None] | None = None
        offset = 0
        scheduled_ns: int | None = None
        found_speech = False
        underrun = False

        def callback(
            outdata: npt.NDArray[np.int16], frames: int, timing: AudioClock, status: OutputStatus
        ) -> None:
            nonlocal current, offset, scheduled_ns, found_speech, underrun
            underrun |= status.output_underflow
            outdata.fill(0)
            written = 0
            while written < frames:
                if current is None:
                    try:
                        current = queue.get_nowait()
                    except Empty:
                        underrun = True
                        return
                    if current is None:
                        raise sd.CallbackStop
                    offset = 0
                samples, first = current
                count = min(frames - written, len(samples) - offset)
                if not found_speech and first is not None and offset <= first < offset + count:
                    scheduled_ns = scheduled_output(timing, written + first - offset, output_rate)
                    found_speech = True
                outdata[written : written + count, 0] = samples[offset : offset + count]
                written += count
                offset += count
                if offset == len(samples):
                    current = None

        with sd.OutputStream(
            samplerate=output_rate,
            channels=1,
            dtype="int16",
            device=device,
            latency="high",
            callback=callback,
            finished_callback=finished.set,
        ):
            for pcm in chunks:
                enqueue(pcm)
            queue.put(None)
            finished.wait()
        if underrun:
            print("Speaker stream underrun: playback may have paused.", flush=True)
        return scheduled_ns
    except sd.PortAudioError as error:
        raise OSError(f"Speaker {device if device is not None else 'default'}: {error}") from error


def report_stream_timing(
    timing: VoiceTiming, first_audio_ns: int, scheduled_ns: int | None
) -> None:
    message = (
        f"Voice timing ({timing.engine}, {timing.words} words, streaming) | "
        f"first chunk {first_audio_ns / 1e9:.3f} s | synthesis {timing.synthesis_ns / 1e9:.3f} s | "
        f"audio length {timing.audio_seconds:.3f} s"
    )
    if scheduled_ns is not None and scheduled_ns - timing.started_ns >= first_audio_ns:
        message += (
            f" | synthesis start to speaker {(scheduled_ns - timing.started_ns) / 1e9:.3f} s "
            "(estimated)"
        )
    else:
        message += " | speaker timing unavailable (check output device and system clocks)"
    print(message, flush=True)


def report_voice_timing(timing: VoiceTiming, scheduled_ns: int | None) -> None:
    synthesis = timing.synthesis_ns / 1e9
    message = (
        f"Voice timing ({timing.engine}, {timing.words} words) | "
        f"synthesis {synthesis:.3f} s | audio length {timing.audio_seconds:.3f} s"
    )
    if scheduled_ns is None:
        message += " | speaker timing unavailable"
    elif scheduled_ns - timing.started_ns < timing.synthesis_ns:
        message += " | speaker timing unavailable (check both system clocks)"
    else:
        elapsed = (scheduled_ns - timing.started_ns) / 1e9
        after_synthesis = elapsed - synthesis
        message += (
            f" | synthesis start to speaker {elapsed:.3f} s (estimated)"
            f" | after synthesis {after_synthesis:.3f} s"
        )
    print(message, flush=True)


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
    on_listening: Callable[[], None] | None = None,
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
    if on_listening is not None and (wake is not None or not quiet):
        on_listening()
        on_listening = None
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
            )
        else:
            audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=limit)
            pcm = audio.get_raw_data(convert_rate=16000, convert_width=2)
    # SpeechRecognition's chunk boundaries can run slightly over its time cap.
    if feedback is not None and not quiet:
        feedback.recorded()
    return write_wav(pcm[: int(limit * 16000) * 2], 16000)


def audio_selector(value: str) -> int | str:
    try:
        return int(value)
    except ValueError:
        if not value.strip():
            raise argparse.ArgumentTypeError("Enter a device index or name") from None
        return value.strip()


def resolve_audio_name(
    name: str, devices: Iterable[tuple[int, str]], *, option: str, list_command: str
) -> int:
    def stable_name(value: str) -> str:
        # PortAudio's ALSA names can embed the changing card index.
        return re.sub(r"\(hw:\d+,(\d+)\)$", r"(hw:*,\1)", value.strip()).casefold()

    wanted = stable_name(name)
    exact = []
    partial = []
    for index, label in devices:
        candidate = stable_name(label)
        if wanted == candidate:
            exact.append(index)
        elif wanted in candidate:
            partial.append(index)
    matches = exact or partial
    if len(matches) == 1:
        return matches[0]
    reason = f"matches indices {', '.join(map(str, matches))}" if matches else "was not found"
    raise ValueError(f"{option} {name!r} {reason}. Check {list_command} for a unique device name.")


def resolve_microphone(name: str) -> int:
    import speech_recognition as sr

    audio = sr.Microphone.get_pyaudio().PyAudio()
    try:
        devices = []
        for index in range(audio.get_device_count()):
            info = audio.get_device_info_by_index(index)
            if info["maxInputChannels"] > 0:
                devices.append((index, str(info["name"])))
    finally:
        audio.terminate()
    return resolve_audio_name(
        name,
        devices,
        option="--microphone",
        list_command="python iris.py client --list-microphones",
    )


def resolve_speaker(name: str) -> int:
    import sounddevice as sd

    devices = (
        (index, str(info["name"]))
        for index, info in enumerate(sd.query_devices())
        if info["max_output_channels"] > 0
    )
    return resolve_audio_name(
        name, devices, option="--speaker", list_command="python -m sounddevice"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server", default="http://127.0.0.1:8765", help="Iris server URL (default: this machine)"
    )
    parser.add_argument("--list-microphones", action="store_true", help="List microphone indices")
    parser.add_argument("--text", action="store_true", help="Test transport without any audio")
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
    parser.add_argument(
        "--test-microphone",
        action="store_true",
        help="Record and play one local test; no server, key, wake model or saved files needed",
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
    parser.add_argument(
        "--show-private-text",
        action="store_true",
        help="Display private conversation text in this terminal (may be logged)",
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
        if args.face and not 1 <= args.face_port <= 65535:
            raise ValueError("--face-port must be between 1 and 65535")
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
        if args.play_wav is not None and (args.text or args.test_microphone):
            raise ValueError("--play-wav cannot be combined with --text or --test-microphone")
        if args.test_microphone and args.text:
            raise ValueError("--test-microphone cannot be combined with --text")
        if not args.text:
            if isinstance(args.speaker, str):
                name = args.speaker
                args.speaker = resolve_speaker(name)
                print(f"Speaker {name!r} resolved to index {args.speaker}.")
            if args.play_wav is None and isinstance(args.microphone, str):
                name = args.microphone
                args.microphone = resolve_microphone(name)
                print(f"Microphone {name!r} resolved to index {args.microphone}.")
        if args.play_wav is not None:
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
            display = FaceDisplay(args.face, args.face_idle)
            with (
                httpx.Client(
                    trust_env=False, follow_redirects=False, timeout=httpx.Timeout(600, connect=10)
                ) as transport,
                display.serve(args.face_port) as face_port,
            ):
                if face_port is not None:
                    print(
                        f"Iris face: open http://127.0.0.1:{face_port}/ on this display.",
                        flush=True,
                    )

                def show_listening() -> None:
                    display.show("listening")

                capture_options: CaptureOptions = {}
                if vad is not None:
                    capture_options["vad"] = vad
                    capture_options["vad_threshold"] = args.vad_threshold
                if face_port is not None:
                    capture_options["on_listening"] = show_listening

                def stream_player(start: VoiceStreamStart, chunks: Iterator[bytes]) -> int | None:
                    feedback.stop_working()
                    with display.speaking(start.reply.expression):
                        return play_stream(
                            chunks,
                            start.rate,
                            args.speaker,
                            lead_in=args.speech_lead_in,
                            primer=args.speech_primer,
                        )

                stream_replies = not (args.text or args.no_speech_stream or args.save_reply)
                client = DroidClient(
                    args.server,
                    os.environ.get("IRIS_DROID_TOKEN", ""),
                    transport,
                    wake_conversation=args.activation == "wake" and not args.text,
                    trace_voice=args.trace_voice,
                    stream_player=stream_player if stream_replies else None,
                    face=face_port is not None,
                    volume_backend=make_volume_backend(
                        args.volume_backend, card=args.alsa_card, control=args.alsa_control
                    ),
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
                    if not args.text:
                        display.show("idle")
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
                                **capture_options,
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
                        display.show("thinking")
                        with nullcontext() if proactive else feedback.working():
                            reply = client.turn(
                                body,
                                text_only=args.text,
                                **({"companion_idle": True} if proactive else {}),
                            )
                    except httpx.HTTPError as error:
                        display.show("idle")
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
                    private = client.private_mode is True
                    if private and not args.show_private_text:
                        print("Private conversation: transcript hidden.")
                    else:
                        print(
                            f"Iris: {reply.text}"
                            if proactive
                            else f"You: {reply.heard}\nIris: {reply.text}"
                        )
                    if (
                        not reply.audio
                        and (
                            is_clear_context(reply.heard)
                            or (conversation is not None and is_stop_listening(reply.heard))
                        )
                        and not getattr(reply, "_streamed", False)
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
                        cue = reply.expression if face_port is not None else None
                        with display.speaking(cue):
                            play_wav(
                                reply_audio,
                                args.speaker,
                                lead_in=args.speech_lead_in,
                                primer=args.speech_primer,
                                **playback_trace,
                            )
                    elif face_port is not None and args.text and reply.text:
                        display.show("listening", reply.expression, seconds=3)
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
