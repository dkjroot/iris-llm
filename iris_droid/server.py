"""PC-side recognition, conversation and synthesis, with no local audio devices."""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import logging
import os
import re
import sqlite3
from collections.abc import Generator
from contextlib import ExitStack, closing, suppress
from http.server import BaseHTTPRequestHandler
from itertools import chain
from pathlib import Path
from threading import BoundedSemaphore, Lock
from time import monotonic, perf_counter_ns, time_ns
from urllib.parse import unquote
from uuid import UUID

import httpx
import speech_recognition as sr

from iris_app.decisions import VoiceDecision as VoiceDecision
from iris_app.replies import STREAM_REPLY as STREAM_REPLY
from iris_app.runtime import Brain as Brain
from iris_chat_engines.compatible import ChatError
from iris_domain.replies import COMMAND_FEEDBACK_HEADER, CommandFeedback
from iris_droid.admission import BoundedHTTPServer, QueueTimeout, TurnQueue
from iris_droid.expression import FACE_HEADER
from iris_droid.face_display import BROWSER_FACE_ASSETS
from iris_droid.personalities import (
    MAX_PERSONALITY_HEADER,
    PERSONALITY_HEADER,
    WEB_PERSONALITY_HEADER,
    PersonalityContext,
    load_personalities,
    personality_catalog,
)
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
    VoiceTiming,
    validate_token,
)
from iris_droid.volume import VolumeResult
from iris_memory.controls import MemoryPolicyError
from iris_memory.embeddings import EmbeddingError
from iris_paths import server_config
from iris_settings import load_settings
from iris_stt_engines import make_stt
from iris_tts_engines import open_tts
from iris_tts_engines.text import plain_speech_text
from iris_tts_engines.worker import SpeechWorker
from iris_turn import (
    TurnExpired,
    check_deadline,
    configure_diagnostics,
    diagnostic_turn,
    remaining_timeout,
    turn_budget,
)

LOGGER = logging.getLogger(__name__)

WEB_ASSETS = {
    **BROWSER_FACE_ASSETS,
    "/chat": ("index.html", "text/html; charset=utf-8"),
    "/chat.css": ("chat.css", "text/css; charset=utf-8"),
    "/chat-controller.js": ("chat-controller.js", "text/javascript; charset=utf-8"),
    "/chat.js": ("chat.js", "text/javascript; charset=utf-8"),
    "/mic.js": ("mic.js", "text/javascript; charset=utf-8"),
    "/speech.js": ("speech.js", "text/javascript; charset=utf-8"),
}
WEB_PERSONALITY_PROMPTS: dict[str, object] = {
    "Assistant": (
        "Be a helpful assistant. Give clear, practical answers and keep everyday requests brief. "
        "When asked to control a home device, use the available home tools and report the result "
        "plainly."
    ),
    "Friend": (
        "Talk like a warm, easygoing friend. Be curious about what the user is telling you, "
        "and keep the conversation natural. Help with practical things when asked."
    ),
    "Comedian": (
        "Be playful and quick with a joke, especially when the user is in on it. Keep facts "
        "and the results of any actions clear, and take your cue from the user's mood."
    ),
}


def handler_for(
    brain: Brain,
    token: str,
    *,
    web_chat: bool = False,
    web_personalities: dict[str, PersonalityContext] | None = None,
) -> type[BaseHTTPRequestHandler]:
    personalities = (
        personality_catalog(WEB_PERSONALITY_PROMPTS)
        if web_personalities is None and web_chat
        else web_personalities or {}
    )
    default_personality = next(iter(personalities.values()), None)
    turn_queue = TurnQueue()
    admission = BoundedSemaphore(brain.settings.max_pending_turns)
    decisions: dict[tuple[str, str], VoiceDecision] = {}
    decisions_lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 deliberately closes each connection.
        _decision: VoiceDecision | None = None

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, format: str, *args: object) -> None:
            pass  # Don't log conversation bodies, credentials or request headers.

        def respond(
            self,
            status: int,
            body: bytes,
            *,
            private: bool | None = None,
            content_type: str = "application/json",
            voice_timing: VoiceTiming | None = None,
            reply: TurnReply | None = None,
        ) -> None:
            if self._decision is not None:
                self._decision.check()
                if status == 200:
                    final = self.completion(reply) if reply is not None else {}
                    self.send_event(
                        "result",
                        reply=json.loads(body),
                        private=private,
                        timing=voice_timing.model_dump() if voice_timing is not None else None,
                        **final,
                    )
                else:
                    self._decision.fail(lambda: self.send_event("failed", status=status))
                return
            if status == 200:
                self.connection.settimeout(remaining_timeout(15))
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if content_type.startswith("text/html"):
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src data:; object-src 'none'; "
                    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
                )
                self.send_header("Permissions-Policy", "microphone=(self)")
            if private is not None:
                self.send_header("X-Iris-Private", "1" if private else "0")
            if voice_timing is not None:
                self.send_header(VOICE_TIMING_HEADER, voice_timing.model_dump_json())
            if reply is not None:
                if self.headers.get(COMPLETION_HEADER) == "1":
                    self.send_header(COMPLETION_HEADER, "1")
                if reply._exchange_id is not None:
                    self.send_header("X-Iris-Exchange", reply._exchange_id)
                if reply._deleted_exchange_id is not None:
                    self.send_header("X-Iris-Deleted-Exchange", reply._deleted_exchange_id)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def send_event(self, event: str, **data: object) -> None:
            frame = json.dumps({"event": event, **data}, separators=(",", ":"))
            self.wfile.write((frame + "\n").encode())

        def reply_payload(self, reply: TurnReply) -> dict[str, object]:
            return reply.model_dump(
                exclude_none=True,
                exclude={"command"} if self.headers.get(COMMAND_FEEDBACK_HEADER) != "1" else set(),
            )

        def completion(self, reply: TurnReply) -> dict[str, object]:
            if self.headers.get(COMPLETION_HEADER) != "1":
                return {}
            return {
                "completion": TurnCompletion(
                    outcome="committed",
                    private=brain.private_mode,
                    exchange_id=reply._exchange_id,
                    deleted_exchange_id=reply._deleted_exchange_id,
                    command=reply.command,
                ).model_dump(
                    exclude={"command"}
                    if self.headers.get(COMMAND_FEEDBACK_HEADER) != "1"
                    else set()
                )
            }

        def start_voice_stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", VOICE_STREAM_TYPE)
            self.send_header("Cache-Control", "no-store")
            if self.headers.get(COMPLETION_HEADER) == "1":
                self.send_header(COMPLETION_HEADER, "1")
            self.send_header("X-Iris-Private", "1" if brain.private_mode else "0")
            self.end_headers()

        def do_GET(self) -> None:
            if self.path == "/v1/health":
                if not self.authenticated():
                    return
                speech = (
                    brain.tts.health()
                    if isinstance(brain.tts, SpeechWorker)
                    else {"state": "in_process"}
                )
                with turn_queue.condition:
                    queue = {
                        "active": bool(turn_queue.waiters),
                        "waiting": max(0, len(turn_queue.waiters) - 1),
                    }
                status = (
                    "stopping"
                    if brain.lifetime.stopping.is_set()
                    else "degraded"
                    if speech["state"] in {"failed", "closed"}
                    else "ready"
                )
                health: dict[str, object] = {"status": status, "queue": queue, "speech": speech}
                instance = os.environ.get("IRIS_INSTANCE_ID", "")
                if re.fullmatch(r"[0-9a-f]{32}", instance):
                    health["instance"] = instance
                self.respond(200, json.dumps(health).encode())
                return
            if web_chat and self.path == "/v1/web-personalities":
                if web_personalities is None or self.authenticated():
                    self.respond(200, json.dumps(list(personalities)).encode())
                return
            asset = WEB_ASSETS.get(self.path) if web_chat else None
            if asset is None:
                self.respond(404, b'{"error":"Unknown route"}')
                return
            name, content_type = asset
            body = Path(__file__).with_name("web").joinpath(name).read_bytes()
            if name == "index.html":
                body = body.replace(
                    b"__IRIS_PHRASE_LIMIT__", str(brain.settings.phrase_time_limit).encode()
                )
                body = body.replace(
                    b"__IRIS_TURN_TIMEOUT__", str(brain.settings.turn_timeout + 5).encode()
                )
            self.respond(200, body, content_type=content_type)

        def authenticated(self) -> bool:
            if hmac.compare_digest(
                self.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()
            ):
                return True
            self.respond(401, b'{"error":"Authentication required"}')
            return False

        def do_POST(self) -> None:
            if not self.authenticated():
                return
            if self.path == VOICE_DECISION_PATH:
                self.handle_decision()
                return
            if self.path not in {"/v1/voice", VOICE_CANCEL_PATH, "/v1/text", "/v1/volume-result"}:
                self.respond(404, b'{"error":"Unknown route"}')
                return
            if not admission.acquire(blocking=False):
                self.respond(503, b'{"error":"Iris is busy; no turn was started"}')
                return
            try:
                with (
                    diagnostic_turn(),
                    brain.lifetime.lease(),
                    turn_budget(brain.settings.turn_timeout),
                ):
                    request = self.read_turn()
                    if request is not None:
                        # Reading uploads never occupies the brain. The queue
                        # preserves order among fully received requests.
                        with turn_queue.enter(brain.settings.queue_timeout):
                            self.handle_turn(*request)
            except QueueTimeout:
                self.respond(503, b'{"error":"Iris is busy; no turn was started"}')
            except TurnExpired:
                self.respond(504, b'{"error":"Turn timed out; check any action before retrying"}')
            except TimeoutError:
                self.respond(408, b'{"error":"Request upload timed out"}')
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            finally:
                admission.release()

        def turn_key(self) -> tuple[str, str]:
            return (
                str(UUID(self.headers.get("X-Iris-Session", ""))),
                str(UUID(self.headers.get(VOICE_TURN_HEADER, ""))),
            )

        def handle_decision(self) -> None:
            try:
                key = self.turn_key()
                if (
                    self.headers.get("Transfer-Encoding")
                    or self.headers.get("Content-Length") != "6"
                ):
                    raise ValueError("Invalid decision length")
                command = self.rfile.read(6)
                if command not in {b"accept", b"cancel"}:
                    raise ValueError("Invalid decision")
            except ValueError:
                self.respond(400, b'{"error":"Invalid voice turn decision"}')
                return
            with decisions_lock:
                decision = decisions.get(key)
            decided = decision is not None and decision.decide(command == b"accept")
            if decided:
                self.respond(204, b"")
            else:
                self.respond(409, b'{"error":"Voice turn is no longer pending"}')

        def handle_turn(
            self, session: str, body: bytes, text_only: bool, companion_idle: bool
        ) -> None:
            stream_timing: VoiceTiming | None = None
            self._stream_started = False

            def stream_reply(reply: TurnReply, chunks: Generator[bytes], rate: int) -> None:
                nonlocal stream_timing
                words = len(re.findall(r"[^\W_]+(?:[’'-][^\W_]+)*", plain_speech_text(reply.text)))
                started_ns = time_ns()
                started = perf_counter_ns()
                size = 0
                with closing(chunks):
                    first = next(chunks, None)
                    check_deadline()
                    if first is None:
                        raise RuntimeError("Pocket TTS returned no audio.")
                    first_audio_ns = perf_counter_ns() - started
                    if self._decision is not None:
                        self._decision.require_acceptance()
                    else:
                        self.start_voice_stream()
                    self._stream_started = True
                    self.send_event(
                        "start",
                        reply=reply.model_dump(exclude_none=True, exclude={"command"}),
                        rate=rate,
                        started_ns=started_ns,
                        first_audio_ns=first_audio_ns,
                    )
                    for pcm in chain((first,), chunks):
                        self.connection.settimeout(remaining_timeout(15))
                        size += len(pcm)
                        if size > MAX_BODY:
                            raise RuntimeError("Pocket TTS reply exceeds the transport limit.")
                        self.send_event("audio", pcm=base64.b64encode(pcm).decode("ascii"))
                stream_timing = VoiceTiming(
                    engine=brain.settings.tts_engine,
                    words=words,
                    started_ns=started_ns,
                    synthesis_ns=perf_counter_ns() - started,
                    audio_seconds=size / (2 * rate),
                )

            use_stream = (
                brain.settings.tts_engine == "pocket"
                and self.headers.get("Accept") == VOICE_STREAM_TYPE
                and callable(getattr(brain.tts, "stream", None))
            )
            token = STREAM_REPLY.set(stream_reply if use_stream else None)
            try:
                reply = self.deliver_turn(session, body, text_only, companion_idle)
                if self._stream_started:
                    assert stream_timing is not None
                    assert reply is not None
                    self.connection.settimeout(remaining_timeout(15))
                    self.send_event(
                        "done", timing=stream_timing.model_dump(), **self.completion(reply)
                    )
            except TurnCancelled:
                pass  # The decision already sent the terminal frame.
            except TurnExpired:
                self.connection.settimeout(1)
                if self._stream_started:
                    self.send_event("error")
                elif self._decision is not None:
                    self._decision.fail(lambda: self.send_event("failed", status=504))
                else:
                    raise
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as error:
                if not self._stream_started:
                    raise
                LOGGER.warning("Droid voice stream failed (%s)", type(error).__name__)
                with suppress(OSError):
                    self.send_event("error")
                self.close_connection = True
            finally:
                STREAM_REPLY.reset(token)
                if self._decision is not None:
                    with decisions_lock:
                        del decisions[self.turn_key()]

        def begin_cancellable_turn(self) -> VoiceDecision:
            key = self.turn_key()
            decision = VoiceDecision(self.send_event)
            with decisions_lock:
                decisions[key] = decision
                self._decision = decision
                self.start_voice_stream()
                self.send_event("processing")
            return decision

        def read_turn(self) -> tuple[str, bytes, bool, bool] | None:
            text_only = self.path == "/v1/text"
            companion_idle = self.headers.get("X-Iris-Companion-Idle") == "1"
            expected = (
                "application/json"
                if self.path == "/v1/volume-result"
                else "text/plain"
                if text_only or companion_idle
                else "audio/wav"
            )
            if self.headers.get_content_type() != expected:
                self.respond(415, b'{"error":"Unexpected content type"}')
                return None
            try:
                session = str(UUID(self.headers.get("X-Iris-Session", "")))
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_BODY:
                    self.respond(413, b'{"error":"Missing or oversized body"}')
                    return None
                expires = monotonic() + remaining_timeout(brain.settings.upload_timeout)
                body = bytearray()
                while len(body) < length:
                    remaining = expires - monotonic()
                    if remaining <= 0:
                        raise TimeoutError("Upload deadline expired")
                    self.connection.settimeout(min(remaining, remaining_timeout(15)))
                    chunk = self.rfile.read1(min(65536, length - len(body)))
                    if not chunk:
                        raise ValueError("Incomplete request")
                    body.extend(chunk)
                self.connection.settimeout(remaining_timeout(15))
                return session, bytes(body), text_only, companion_idle
            except (ValueError, EOFError):
                self.respond(400, b'{"error":"Invalid session or request body"}')
                return None

        def deliver_turn(
            self, session: str, body: bytes, text_only: bool, companion_idle: bool
        ) -> TurnReply | None:
            try:
                check_deadline()
                if self.path == "/v1/volume-result":
                    brain._sync_policy()
                    result = VolumeResult.model_validate_json(body)
                    chat = brain.sessions.get(session)
                    reply = brain._reply(
                        "",
                        result.description(),
                        text_only=self.headers.get("X-Iris-Text-Only") == "1",
                        companion=chat is not None and chat.companion_mode,
                    )
                    reply.command = CommandFeedback(
                        title="Speaker volume",
                        message=result.description(),
                        status="success" if result.state is not None else "error",
                    )
                else:
                    if self.path == VOICE_CANCEL_PATH and companion_idle:
                        raise ValueError("Companion openers do not support cancellation")
                    reply = self.run_voice_or_text(session, body, text_only, companion_idle)
            except (TurnCancelled, TurnExpired):
                raise
            except (ValueError, EOFError):
                if self._stream_started:
                    raise
                self.respond(400, b'{"error":"Invalid session, text or WAV recording"}')
                return None
            except sr.UnknownValueError:
                if self._stream_started:
                    raise
                self.respond(422, b'{"error":"No speech recognized; please try again"}')
                return None
            except sqlite3.Error as error:
                if self._stream_started:
                    raise
                check_deadline()
                LOGGER.error("Conversation memory failed (%s)", type(error).__name__)
                self.respond(
                    503, b'{"error":"Conversation memory unavailable; check the PC archive"}'
                )
                return None
            except MemoryPolicyError:
                if self._stream_started:
                    raise
                self.respond(
                    503, b'{"error":"Conversation memory policy changed; please try again"}'
                )
                return None
            except ChatError as error:
                if self._stream_started:
                    raise
                if brain.private_mode or (
                    self._decision is not None and not self._decision.accepted
                ):
                    LOGGER.warning("Chat failed (%s)", type(error).__name__)
                else:
                    LOGGER.warning("Chat failed: %s", error)
                self.respond(502, b'{"error":"Chat failed; check the PC model server"}')
                return None
            except Exception as error:
                if self._stream_started:
                    raise
                LOGGER.error("Droid turn failed (%s)", type(error).__name__)
                self.respond(503, b'{"error":"Speech service failed; check the PC console"}')
                return None
            check_deadline()
            if self._stream_started:
                return reply
            self.respond(
                200,
                json.dumps(self.reply_payload(reply), ensure_ascii=False).encode(),
                private=brain.private_mode,
                reply=reply,
                voice_timing=(
                    reply._voice_timing if self.headers.get(VOICE_TIMING_HEADER) == "1" else None
                ),
            )
            return reply

        def run_voice_or_text(
            self, session: str, body: bytes, text_only: bool, companion_idle: bool
        ) -> TurnReply:
            personality = None
            if (value := self.headers.get(PERSONALITY_HEADER)) is not None:
                if len(value) > MAX_PERSONALITY_HEADER or not value.isascii():
                    raise ValueError("Invalid personality header")
                personality = PersonalityContext.model_validate_json(value)
            elif web_chat and (name := self.headers.get(WEB_PERSONALITY_HEADER)) is not None:
                # The browser supplies only the name. Prompts stay on the server.
                if len(name) > 576 or not name.isascii():
                    raise ValueError("Invalid web personality header")
                personality = (
                    personalities.get(unquote(name, errors="strict"), default_personality)
                    if name
                    else default_personality
                )

            def run(decision: VoiceDecision | None = None) -> TurnReply:
                return brain.turn(
                    session,
                    body,
                    text_only=text_only,
                    companion_idle=companion_idle,
                    wake_conversation=self.headers.get("X-Iris-Wake-Conversation") == "1",
                    volume_supported=self.headers.get("X-Iris-Volume") == "1",
                    face=self.headers.get(FACE_HEADER) == "1",
                    decision=decision,
                    personality=personality,
                )

            if self.path != VOICE_CANCEL_PATH:
                return run()
            return run(self.begin_cancellable_turn())

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--web-chat", action="store_true", help="Serve the browser chat at /chat")
    parser.add_argument(
        "--web-personalities-file",
        type=Path,
        help="JSON file with the browser's personality prompts",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    configure_diagnostics()
    try:
        token = os.environ.get("IRIS_DROID_TOKEN", "")
        validate_token(token)
        settings = load_settings(server_config(args.config))
        web_personalities = (
            load_personalities(args.web_personalities_file)
            if args.web_personalities_file is not None
            else None
        )
        with ExitStack() as resources:
            client = resources.enter_context(httpx.Client(trust_env=False, follow_redirects=False))
            tts = resources.enter_context(open_tts(settings))
            brain = Brain(settings, client, make_stt(settings), tts)
            brain.lifetime.own(resources.pop_all().close)
            try:
                handler = handler_for(
                    brain, token, web_chat=args.web_chat, web_personalities=web_personalities
                )
                with BoundedHTTPServer((args.host, args.port), handler) as server:
                    print(f"Iris droid server listening on {args.host}:{args.port}")
                    server.serve_forever()
            finally:
                if not brain.lifetime.close():
                    LOGGER.warning(
                        "Shutdown is waiting for native inference to release its resources."
                    )
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, ImportError, sqlite3.Error, EmbeddingError) as error:
        print(f"Droid server setup: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
