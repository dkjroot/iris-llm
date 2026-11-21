"""HTTP and NDJSON turn transport between the Iris droid client and server."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError

from iris_domain.replies import COMMAND_FEEDBACK_HEADER, CommandFeedback
from iris_droid.controller import ClientController
from iris_droid.expression import FACE_HEADER
from iris_droid.feedback import ClientFeedback
from iris_droid.personalities import PERSONALITY_HEADER, load_personalities
from iris_droid.playback import report_stream_timing
from iris_droid.protocol import (
    COMPLETION_HEADER,
    MAX_BODY,
    STREAM_TEXT_HEADER,
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
    validate_token,
)
from iris_droid.volume import VolumeBackend, execute_volume, volume_command
from iris_frames import ndjson_lines


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
        self.headers[COMMAND_FEEDBACK_HEADER] = "1"
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
        if (
            reply.selected_personality is not None
            and self.personality != reply.selected_personality
        ):
            reply.command = CommandFeedback(
                title="Personality unchanged",
                message=f"Keeping your newer selection: {self.personality}.",
                status="info",
            )
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
            reply.command = CommandFeedback(
                title="Speaker volume",
                message=result.description(),
                status="success" if result.state is not None else "error",
            )
        return reply

    def _request(self, path: str, body: bytes, headers: dict[str, str]) -> TurnReply:
        headers = {**headers, COMPLETION_HEADER: "1", "Accept-Encoding": "identity"}
        if self.trace_voice:
            headers = {**headers, VOICE_TIMING_HEADER: "1"}
        if path != "/v1/text" and self.stream_player is not None:
            headers = {**headers, "Accept": VOICE_STREAM_TYPE, STREAM_TEXT_HEADER: "1"}
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
        if completion.command is not None:
            reply.command = completion.command
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
            if isinstance(completion.get("text"), str):
                start.reply.text = str(completion["text"])
            self._complete(start.reply, completion, response)
        except ValueError as error:
            raise httpx.RemoteProtocolError(str(error), request=response.request) from error
        if self.trace_voice:
            report_stream_timing(completed, start.first_audio_ns, scheduled_ns)
        start.reply._streamed = True
        return start.reply
