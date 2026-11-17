"""Droid-side microphone, optional local wake detection, and speaker playback."""

from __future__ import annotations

import base64
import json
import os
import re
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from functools import partial
from importlib.resources import files
from math import isfinite
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, Thread
from time import monotonic, time_ns
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
from iris_droid.client_settings import ClientOptions, parse_client_args
from iris_droid.companion import CompanionTimer, ConsoleInput
from iris_droid.controller import ClientController
from iris_droid.conversation import WakeConversation
from iris_droid.expression import FACE_HEADER
from iris_droid.face_display import FaceDisplay
from iris_droid.feedback import ClientFeedback
from iris_droid.personalities import PERSONALITY_HEADER, load_personalities
from iris_droid.protocol import (
    COMPLETION_HEADER,
    MAX_BODY,
    VOICE_CANCEL_PATH,
    VOICE_DECISION_PATH,
    VOICE_STREAM_TYPE,
    VOICE_TIMING_HEADER,
    VOICE_TURN_HEADER,
    TurnCancelled,
    TurnCompletion,
    TurnReply,
    VoiceStreamStart,
    VoiceTiming,
    is_clear_context,
    is_delete_previous,
    is_stop_listening,
    read_wav,
    validate_token,
    write_wav,
)
from iris_droid.volume import VolumeBackend, execute_volume, make_volume_backend, volume_command
from iris_droid.wake import WakeDetector
from iris_frames import ndjson_lines

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
        cancel_listener: Callable[[Event], bool] | None = None,
        cancel_feedback: ClientFeedback | None = None,
        personalities_file: Path | None = None,
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
        self.volume_backend = volume_backend
        self.trace_voice = trace_voice
        self.stream_player = stream_player
        self.cancel_listener = cancel_listener
        self.cancel_feedback = cancel_feedback
        self.personality_headers = {
            name: context.to_header()
            for name, context in load_personalities(personalities_file).items()
        }
        self.state = ClientController(self.personality_headers)
        self.headers = {"Authorization": f"Bearer {token}", "X-Iris-Session": str(uuid4())}
        self.headers["X-Iris-Volume"] = "1"
        if wake_conversation:
            self.headers["X-Iris-Wake-Conversation"] = "1"
        if face:
            self.headers[FACE_HEADER] = "1"

    @property
    def private_mode(self) -> bool:
        return self.state.private_mode

    @private_mode.setter
    def private_mode(self, value: bool) -> None:
        self.state.private_mode = value

    @property
    def personality(self) -> str | None:
        return self.state.selection()[0]

    def select_personality(self, name: str) -> bool:
        return self.state.select(name)

    def turn(
        self, body: bytes, *, text_only: bool = False, companion_idle: bool = False
    ) -> TurnReply:
        with self.state.turn():
            return self._turn(body, text_only=text_only, companion_idle=companion_idle)

    def _turn(
        self, body: bytes, *, text_only: bool = False, companion_idle: bool = False
    ) -> TurnReply:
        if not 0 < len(body) <= MAX_BODY:
            raise ValueError("Turn is empty or exceeds the transport size limit")
        headers = {
            **self.headers,
            "Content-Type": "text/plain" if text_only or companion_idle else "audio/wav",
        }
        personality, personality_revision = self.state.selection()
        if personality is not None:
            headers[PERSONALITY_HEADER] = self.personality_headers[personality]
        if companion_idle:
            headers["X-Iris-Companion-Idle"] = "1"
        path = "/v1/text" if text_only else "/v1/voice"
        if self.cancel_listener is not None and not (text_only or companion_idle):
            path = VOICE_CANCEL_PATH
            headers[VOICE_TURN_HEADER] = str(uuid4())
        try:
            reply = self._request(path, body, headers)
        except TurnCancelled:
            if self.cancel_feedback is not None:
                self.cancel_feedback.cancelled()
            raise
        self.state.acknowledge(reply, personality_revision, companion_idle=companion_idle)
        if reply.volume is not None:
            if companion_idle or reply.volume != volume_command(reply.heard):
                raise ValueError("Unexpected speaker volume action")
            result = execute_volume(self.volume_backend, reply.volume)
            # Report the actual read-back, never an assumed success. This second
            # request only synthesizes a fixed acknowledgement; it cannot act.
            heard = reply.heard
            print(result.description(), flush=True)
            reply = self._request(
                "/v1/volume-result",
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

    def _request(self, path: str, body: bytes, headers: dict[str, str]) -> TurnReply:
        headers = {**headers, COMPLETION_HEADER: "1", "Accept-Encoding": "identity"}
        if self.trace_voice:
            headers = {**headers, VOICE_TIMING_HEADER: "1"}
        if path != "/v1/text" and self.stream_player is not None:
            headers = {**headers, "Accept": VOICE_STREAM_TYPE}
        with self.client.stream(
            "POST", f"{self.url}{path}", content=body, headers=headers
        ) as response:
            response.raise_for_status()
            if response.headers.get("Content-Encoding", "identity") != "identity":
                raise httpx.RemoteProtocolError("Unexpected encoded Iris response")
            private = response.headers.get("X-Iris-Private")
            if private in {"0", "1"}:
                self.private_mode = private == "1"
            if path == VOICE_CANCEL_PATH:
                return self._cancellable_reply(response, headers[VOICE_TURN_HEADER])
            if response.headers.get("Content-Type") == VOICE_STREAM_TYPE:
                lines = self._frames(response)
                try:
                    return self._stream_reply(response, next(lines, ""), lines)
                except ValueError as error:
                    raise httpx.RemoteProtocolError(str(error), request=response.request) from error
            timing = response.headers.get(VOICE_TIMING_HEADER)
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) > MAX_BODY:
                    raise ValueError("Server response exceeds the transport size limit")
        reply = TurnReply.model_validate_json(bytes(data))
        reply._exchange_id = response.headers.get("X-Iris-Exchange")
        reply._deleted_exchange_id = response.headers.get("X-Iris-Deleted-Exchange")
        if response.headers.get(COMPLETION_HEADER) == "1":
            self._complete(
                reply,
                {
                    "completion": {
                        "outcome": "committed",
                        "private": private == "1" if private in {"0", "1"} else None,
                        "exchange_id": reply._exchange_id,
                        "deleted_exchange_id": reply._deleted_exchange_id,
                    }
                },
                response,
            )
        if self.trace_voice and timing is not None:
            try:
                reply._voice_timing = VoiceTiming.model_validate_json(timing)
            except ValidationError:
                print("Voice timing unavailable: invalid server timing.", flush=True)
        return reply

    @staticmethod
    def _frames(response: httpx.Response) -> Iterator[str]:
        return ndjson_lines(
            response.iter_bytes(),
            max_frame=MAX_BODY * 3 + 1024,
            max_total=MAX_BODY * 4 + 65536,
            max_frames=8192,
        )

    def _complete(
        self, reply: TurnReply, frame: dict[str, object], response: httpx.Response
    ) -> None:
        value = frame.get("completion")
        if value is None and response.headers.get(COMPLETION_HEADER) != "1":
            return  # Older servers do not promise authoritative final metadata.
        completion = TurnCompletion.model_validate(value)
        self.private_mode = completion.private
        reply._completion = completion
        reply._exchange_id = completion.exchange_id
        reply._deleted_exchange_id = completion.deleted_exchange_id

    def _cancellable_reply(self, response: httpx.Response, turn_id: str) -> TurnReply:
        lines = self._frames(response)
        if response.headers.get("Content-Type") != VOICE_STREAM_TYPE:
            raise httpx.RemoteProtocolError("Expected a cancellable voice stream")
        try:
            if json.loads(next(lines, "")) != {"event": "processing"}:
                raise ValueError("Expected the voice processing event")
        except ValueError as error:
            raise httpx.RemoteProtocolError(str(error), request=response.request) from error
        feedback = self.cancel_feedback
        listener = self.cancel_listener
        assert listener is not None
        stop = Event()
        cancelled = Event()
        confirmed = Event()
        retired = Event()

        def decide(command: bytes) -> None:
            result = self.client.post(
                f"{self.url}{VOICE_DECISION_PATH}",
                headers={**self.headers, VOICE_TURN_HEADER: turn_id},
                content=command,
                timeout=5,
            )
            result.raise_for_status()
            if result.status_code != 204:
                raise httpx.RemoteProtocolError("Unexpected voice turn decision response")

        def listen() -> None:
            try:
                matched = listener(stop)
            except Exception:
                print("Cancel listener failed. Discarding this voice turn.", flush=True)
                matched = True
            if matched and not retired.is_set() and not self.state.closed:
                cancelled.set()
                try:
                    decide(b"cancel")
                except httpx.HTTPError:
                    # The processing request can still confirm cancellation or
                    # retry the decision when it reaches the acceptance boundary.
                    return
                confirmed.set()

        with feedback.working() if feedback is not None else nullcontext():
            worker = Thread(target=listen, name="iris-cancel-listener", daemon=True)
            worker.start()
            accepted = False
            try:
                for line in lines:
                    # ASCII JSON escaping can turn a two-byte character into six bytes.
                    if len(line) > MAX_BODY * 3 + 1024:
                        raise ValueError("Oversized voice stream frame")
                    frame = json.loads(line)
                    if not isinstance(frame, dict):
                        raise ValueError("Invalid voice stream frame")
                    event = frame.get("event")
                    if event == "ready" and not accepted:
                        stop.set()
                        worker.join(timeout=6)
                        if worker.is_alive():
                            retired.set()
                            raise ValueError("Cancel listener did not stop; turn not accepted")
                        if feedback is not None:
                            feedback.stop_working()
                        if cancelled.is_set():
                            if not confirmed.is_set():
                                try:
                                    decide(b"cancel")
                                except httpx.HTTPStatusError as error:
                                    if error.response.status_code != 409:
                                        raise
                                    # The first attempt may have cancelled and closed
                                    # the turn before its acknowledgement reached us.
                                else:
                                    confirmed.set()
                        else:
                            decide(b"accept")
                            accepted = True
                    elif event == "cancelled":
                        stop.set()
                        worker.join(timeout=6)
                        if worker.is_alive():
                            retired.set()
                            raise ValueError("Cancel listener did not stop; turn not accepted")
                        raise TurnCancelled()
                    elif event == "failed":
                        stop.set()
                        worker.join(timeout=6)
                        if worker.is_alive():
                            retired.set()
                            raise ValueError("Cancel listener did not stop; turn not accepted")
                        if confirmed.is_set():
                            raise TurnCancelled()
                        status = frame.get("status")
                        if type(status) is not int or not 400 <= status <= 599:
                            raise ValueError("Invalid voice error status")
                        raise httpx.HTTPStatusError(
                            "Voice turn failed",
                            request=response.request,
                            response=httpx.Response(status, request=response.request),
                        )
                    elif event == "start" and accepted:
                        return self._stream_reply(response, line, lines)
                    elif event == "result" and accepted:
                        reply = TurnReply.model_validate(frame.get("reply"))
                        if next(lines, None) is not None:
                            raise ValueError("Voice stream continued after completion")
                        private = frame.get("private")
                        if isinstance(private, bool):
                            self.private_mode = private
                        if self.trace_voice and frame.get("timing") is not None:
                            reply._voice_timing = VoiceTiming.model_validate(frame["timing"])
                        self._complete(reply, frame, response)
                        return reply
                    else:
                        raise ValueError("Unexpected cancellable voice event")
                if confirmed.is_set():
                    raise TurnCancelled()
                raise ValueError("Cancellable voice stream ended before the reply")
            except ValueError as error:
                raise httpx.RemoteProtocolError(str(error), request=response.request) from error
            finally:
                retired.set()
                stop.set()
                worker.join(timeout=6)

    def _stream_reply(
        self, response: httpx.Response, first: str, lines: Iterator[str]
    ) -> TurnReply:
        timing: VoiceTiming | None = None
        completion: dict[str, object] | None = None

        def chunks() -> Iterator[bytes]:
            nonlocal timing, completion
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
                    if next(lines, None) is not None:
                        raise ValueError("Voice stream continued after completion")
                    completion = frame
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
            if len(first) > MAX_BODY:
                raise ValueError("Oversized voice stream header")
            start = VoiceStreamStart.model_validate_json(first)
            scheduled_ns = self.stream_player(start, chunks())
            completed = timing
            if completed is None:
                raise ValueError("Incomplete voice stream")
            assert completion is not None
            self._complete(start.reply, completion, response)
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
            sd.play(samples, rate, device=device, blocking=False, latency="high")
            stream = sd.get_stream()
            expires = monotonic() + len(samples) / rate + 5
            try:
                # play() owns its callback. Watch the public stream state instead
                # of passing a second finished_callback through its keyword args.
                pause = Event()
                while stream.active:
                    remaining = expires - monotonic()
                    if remaining <= 0:
                        stream.abort()
                        raise OSError("Speaker playback did not finish")
                    pause.wait(min(0.05, remaining))
            finally:
                stream.close()
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
        if not finished.wait(len(samples) / rate + 5):
            raise OSError("Speaker playback did not finish")
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
        queued_seconds = lead_in + 2

        def enqueue(pcm: bytes) -> None:
            nonlocal converter, queued_seconds
            queued_seconds += len(pcm) / (rate * 2)
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
            if not finished.wait(queued_seconds + 5):
                raise OSError("Speaker stream did not finish")
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
            with (
                httpx.Client(
                    trust_env=False, follow_redirects=False, timeout=httpx.Timeout(600, connect=10)
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
                    feedback.stop_working()
                    with display.speaking(start.reply.expression):
                        return play_stream(
                            chunks,
                            start.rate,
                            speaker,
                            lead_in=args.speech_lead_in,
                            primer=args.speech_primer,
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
                            play_wav(body, speaker)
                    try:
                        display.show("thinking")
                        with (
                            nullcontext() if proactive or args.voice_cancel else feedback.working()
                        ):
                            reply = client.turn(
                                body,
                                text_only=args.text,
                                **({"companion_idle": True} if proactive else {}),
                            )
                    except TurnCancelled:
                        print("Voice turn discarded.", flush=True)
                        if conversation is not None and conversation.active:
                            conversation.activate()
                        continue
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
                        cue = reply.expression if face_port is not None else None
                        with display.speaking(cue):
                            play_wav(
                                reply_audio,
                                speaker,
                                lead_in=args.speech_lead_in,
                                primer=args.speech_primer,
                                **playback_trace,
                            )
                    elif face_port is not None and args.text and reply.text:
                        display.show("listening", reply.expression, seconds=3)
                    if stop_listening and conversation is not None:
                        companion.update(False)
                        conversation.sleep()
                        print(f'Waiting for "{args.wake_phrase}" again.', flush=True)
                        continue
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
