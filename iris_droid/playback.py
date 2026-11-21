"""Droid-side speaker playback, timing traces and audio device selection."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterable, Iterator
from importlib.resources import files
from math import isfinite
from queue import Empty, SimpleQueue
from threading import Event
from time import monotonic, time_ns
from typing import TYPE_CHECKING, Protocol, TypedDict, cast

from iris_droid.protocol import VoiceTiming, read_wav
from iris_turn import measured

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt


def _client_attr(name: str, default: object) -> object:
    module = sys.modules.get("iris_droid.client")
    return getattr(module, name, default) if module is not None else default


class AudioClock(Protocol):
    @property
    def currentTime(self) -> float: ...

    @property
    def outputBufferDacTime(self) -> float: ...


class OutputStatus(Protocol):
    @property
    def output_underflow(self) -> bool: ...


class AudioAnimation(TypedDict, total=False):
    on_audio: Callable[[npt.NDArray[np.int16], int, float], None]


class PlaybackTrace(TypedDict, total=False):
    on_speech_start: Callable[[int | None], None]


def output_delay(timing: AudioClock) -> float:
    delay = timing.outputBufferDacTime - timing.currentTime
    return delay if isfinite(delay) and 0 <= delay <= 2 else 0.0


def scheduled_output(timing: AudioClock, frame: int, rate: int) -> int | None:
    now_ns = cast(Callable[[], int], _client_attr("time_ns", time_ns))
    callback_ns = now_ns()
    current = timing.currentTime
    dac = timing.outputBufferDacTime
    if isfinite(current) and isfinite(dac) and dac >= current and dac > 0:
        return callback_ns + round((dac - current + frame / rate) * 1e9)
    return None


@measured("playback")
def play_timed(
    samples: npt.NDArray[np.int16],
    rate: int,
    device: int | None,
    speech_frame: int | None,
    on_speech_start: Callable[[int | None], None] | None,
    *,
    on_audio: Callable[[npt.NDArray[np.int16], int, float], None] | None = None,
    prefix_frames: int = 0,
) -> None:
    import sounddevice as sd

    event_cls = cast(type[Event], _client_attr("Event", Event))
    finished = event_cls()
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
        if on_audio is not None:
            visual = outdata[:, 0].copy()
            visual[: max(0, min(count, prefix_frames - position))] = 0
            on_audio(visual, rate, output_delay(timing))
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
    ) as stream:
        if not finished.wait(len(samples) / rate + 5):
            stream.abort()
            raise OSError("Speaker playback did not finish")
    if on_speech_start is not None:
        on_speech_start(scheduled_ns)
    if underrun:
        print("Speaker playback underrun: some audio may have been lost.", flush=True)


def play_wav(
    data: bytes,
    device: int | None = None,
    *,
    loop: bool = False,
    lead_in: float = 0,
    primer: bool = False,
    on_speech_start: Callable[[int | None], None] | None = None,
    on_audio: Callable[[npt.NDArray[np.int16], int, float], None] | None = None,
) -> None:
    if not 0 <= lead_in <= 2:
        raise ValueError("Speech lead-in must be between 0 and 2 seconds")
    import numpy as np
    import sounddevice as sd
    import speech_recognition as sr

    event_cls = cast(type[Event], _client_attr("Event", Event))
    clock = cast(Callable[[], float], _client_attr("monotonic", monotonic))
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
        prefix_frames = sum(len(prefix) for prefix in prefixes)
        if prefixes:
            samples = np.concatenate([*prefixes, samples])
        if loop:
            sd.play(samples, rate, device=device, blocking=False, loop=True, latency="high")
        elif on_speech_start is not None or on_audio is not None:
            play_timed(
                samples,
                rate,
                device,
                speech_frame,
                on_speech_start,
                on_audio=on_audio,
                prefix_frames=prefix_frames,
            )
        else:
            sd.play(samples, rate, device=device, blocking=False, latency="high")
            stream = sd.get_stream()
            expires = clock() + len(samples) / rate + 5
            try:
                # play() owns its callback. Watch the public stream state instead
                # of passing a second finished_callback through its keyword args.
                pause = event_cls()
                while stream.active:
                    remaining = expires - clock()
                    if remaining <= 0:
                        stream.abort()
                        raise OSError("Speaker playback did not finish")
                    pause.wait(min(0.005, remaining))
            finally:
                stream.close()
            status = sd.get_status()
            if status is not None and status.output_underflow:
                print("Speaker playback underrun: some audio may have been lost.", flush=True)
    except sd.PortAudioError as error:
        raise OSError(f"Speaker {device if device is not None else 'default'}: {error}") from error


@measured("playback")
def play_stream(
    chunks: Iterator[bytes],
    rate: int,
    device: int | None,
    *,
    lead_in: float,
    primer: bool,
    before_output: Callable[[], None] | None = None,
    on_audio: Callable[[npt.NDArray[np.int16], int, float], None] | None = None,
) -> int | None:
    import numpy as np
    import sounddevice as sd
    import speech_recognition as sr

    event_cls = cast(type[Event], _client_attr("Event", Event))
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
        # Lead-in silence already supplies playback headroom, so don't double-buffer.
        initial: list[bytes] = []
        size = 0
        target_buffer = max(1, round(rate * max(0.0, 0.16 - lead_in)) * 2)
        while size < target_buffer:
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
        finished = event_cls()
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
            visual = np.zeros(frames, dtype=np.int16) if on_audio is not None else None
            written = 0
            try:
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
                        scheduled_ns = scheduled_output(
                            timing, written + first - offset, output_rate
                        )
                        found_speech = True
                    outdata[written : written + count, 0] = samples[offset : offset + count]
                    if visual is not None and first is not None:
                        visual[written : written + count] = samples[offset : offset + count]
                    written += count
                    offset += count
                    if offset == len(samples):
                        current = None
            finally:
                if on_audio is not None and visual is not None:
                    on_audio(visual, output_rate, output_delay(timing))

        if before_output is not None:
            before_output()
        with sd.OutputStream(
            samplerate=output_rate,
            channels=1,
            dtype="int16",
            device=device,
            latency="high",
            callback=callback,
            finished_callback=finished.set,
        ) as stream:
            try:
                for pcm in chunks:
                    enqueue(pcm)
                queue.put(None)
                if not finished.wait(queued_seconds + 5):
                    stream.abort()
                    raise OSError("Speaker stream did not finish")
            except BaseException:
                stream.abort()
                raise
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
