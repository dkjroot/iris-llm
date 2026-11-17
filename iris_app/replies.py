"""Prepare buffered replies or send audio to a caller-supplied sink."""

from __future__ import annotations

import base64
import re
from collections.abc import Callable, Generator
from contextvars import ContextVar
from time import perf_counter_ns, time_ns
from typing import TYPE_CHECKING, cast

import numpy as np

from iris_audio import write_wav
from iris_domain.audio import StreamingSynthesizer
from iris_domain.expression import FaceExpression
from iris_domain.limits import MAX_BODY
from iris_domain.replies import (
    TurnReply,
    VoiceTiming,
)
from iris_tts_engines.text import plain_speech_text
from iris_turn import check_deadline, stage

if TYPE_CHECKING:
    from iris_app.runtime import Brain
STREAM_REPLY: ContextVar[Callable[[TurnReply, Generator[bytes], int], None] | None] = ContextVar(
    "stream_reply", default=None
)


def timed_audio(chunks: Generator[bytes]) -> Generator[bytes]:
    """Time synthesis between yields, excluding the sink's socket/backpressure wait."""
    try:
        while True:
            with stage("tts"):
                try:
                    chunk = next(chunks)
                except StopIteration:
                    return
            yield chunk
    finally:
        chunks.close()


class ReplyService:
    def __init__(self, app: Brain) -> None:
        self.app = app

    def prepare(
        self,
        heard: str,
        text: str,
        *,
        text_only: bool,
        companion: bool,
        finished: bool = False,
        expression: FaceExpression | None = None,
        selected_personality: str | None = None,
        transactional: bool = False,
    ) -> TurnReply:
        result = TurnReply(
            heard=heard,
            text=text,
            finished=finished,
            companion_mode=companion and not finished,
            expression=expression,
            selected_personality=selected_personality,
        )
        check_deadline()
        stream_reply = STREAM_REPLY.get()
        # Short state-changing confirmations are fully synthesized before the
        # commit, then returned as a buffered reply. A streamed success cannot
        # be retracted if the subsequent database/policy operation fails.
        if not text_only and stream_reply is not None and not transactional:
            tts = cast(StreamingSynthesizer, self.app.tts)
            with stage("stream_delivery"):
                stream_reply(result, timed_audio(tts.stream(text)), tts.sample_rate)
            return result
        audio = ""
        timing = None
        if not text_only:
            started_ns = time_ns()
            started = perf_counter_ns()
            with stage("tts"):
                samples, rate = self.app.tts.synthesize(text)
            check_deadline()
            synthesis_ns = perf_counter_ns() - started
            timing = VoiceTiming(
                engine=self.app.settings.tts_engine,
                words=len(re.findall(r"[^\W_]+(?:[’'-][^\W_]+)*", plain_speech_text(text))),
                started_ns=started_ns,
                synthesis_ns=synthesis_ns,
                audio_seconds=samples.size / rate,
            )
            pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
            audio = base64.b64encode(write_wav(pcm, rate)).decode("ascii")
        result.audio = audio
        result._voice_timing = timing
        if len(result.model_dump_json().encode()) > MAX_BODY:
            raise ValueError("Synthesized reply exceeds the transport size limit")
        return result
