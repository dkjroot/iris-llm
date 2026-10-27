"""PC-side recognition, conversation and synthesis, with no local audio devices."""

from __future__ import annotations

import argparse
import base64
import hmac
import logging
import os
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Protocol
from uuid import UUID

import httpx
import numpy as np
import numpy.typing as npt
import speech_recognition as sr

from iris_chat_engines.compatible import ChatEngine, ChatError
from iris_droid.protocol import MAX_BODY, TurnReply, read_wav, validate_token, write_wav
from iris_settings import Settings, load_settings
from iris_stt_engines import make_stt
from iris_stt_engines.ISTT import ISTT

LOGGER = logging.getLogger(__name__)


class Synthesizer(Protocol):
    def synthesize(self, text: str) -> tuple[npt.NDArray[np.float32], int]: ...


class Brain:
    def __init__(
        self, settings: Settings, client: httpx.Client, stt: ISTT, tts: Synthesizer
    ) -> None:
        self.settings = settings
        self.client = client
        self.stt = stt
        self.tts = tts
        self.sessions: OrderedDict[str, ChatEngine] = OrderedDict()

    def turn(self, session: str, body: bytes, *, text_only: bool = False) -> TurnReply:
        if text_only:
            heard = body.decode("utf-8").strip()
            if len(heard) > 8000:
                raise ValueError("Text turn exceeds 8000 characters")
        else:
            pcm, rate = read_wav(body, input_audio=True)
            if len(pcm) > rate * 2 * self.settings.phrase_time_limit:
                raise ValueError("Recording exceeds the configured phrase_time_limit")
            heard = self.stt.recognize(sr.AudioData(pcm, rate, 2)).strip()
        if not heard:
            raise sr.UnknownValueError()
        finished = heard.casefold().strip(" .!?") == "exit"
        if session not in self.sessions:
            if len(self.sessions) >= 16:
                self.sessions.popitem(last=False)
            self.sessions[session] = ChatEngine(self.settings, self.client)
        self.sessions.move_to_end(session)
        chat = self.sessions[session]
        previous = chat.history.copy()
        try:
            text = "Shutting down" if finished else chat.get_response(heard)
            audio = ""
            if not text_only:
                samples, rate = self.tts.synthesize(text)
                pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
                audio = base64.b64encode(write_wav(pcm, rate)).decode("ascii")
            result = TurnReply(heard=heard, text=text, audio=audio, finished=finished)
            if len(result.model_dump_json().encode()) > MAX_BODY:
                raise ValueError("Synthesized reply exceeds the transport size limit")
        except Exception:
            chat.history = previous
            raise
        if finished:
            del self.sessions[session]
        return result


def handler_for(brain: Brain, token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 deliberately closes each connection. Models run serially.
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, format: str, *args: object) -> None:
            pass  # Don't log conversation bodies, credentials or request headers.

        def respond(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if not hmac.compare_digest(
                self.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()
            ):
                self.respond(401, b'{"error":"Authentication required"}')
                return
            if self.path not in {"/v1/voice", "/v1/text"}:
                self.respond(404, b'{"error":"Unknown route"}')
                return
            text_only = self.path == "/v1/text"
            expected = "text/plain" if text_only else "audio/wav"
            if self.headers.get_content_type() != expected:
                self.respond(415, b'{"error":"Unexpected content type"}')
                return
            try:
                session = str(UUID(self.headers.get("X-Iris-Session", "")))
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_BODY:
                    self.respond(413, b'{"error":"Missing or oversized body"}')
                    return
                body = self.rfile.read(length)
                if len(body) != length:
                    raise ValueError("Incomplete request")
                reply = brain.turn(session, body, text_only=text_only)
            except (ValueError, EOFError):
                self.respond(400, b'{"error":"Invalid session, text or WAV recording"}')
                return
            except sr.UnknownValueError:
                self.respond(422, b'{"error":"No speech recognized; please try again"}')
                return
            except ChatError:
                self.respond(502, b'{"error":"Chat failed; check the PC model server"}')
                return
            except Exception as error:
                LOGGER.error("Droid turn failed (%s)", type(error).__name__)
                self.respond(503, b'{"error":"Speech service failed; check the PC console"}')
                return
            self.respond(200, reply.model_dump_json().encode())

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("local-config.json"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    try:
        token = os.environ.get("IRIS_DROID_TOKEN", "")
        validate_token(token)
        settings = load_settings(args.config if args.config.exists() else None)
        if not args.config.exists() and args.config != Path("local-config.json"):
            raise ValueError("The requested configuration file does not exist")
        from iris_tts_engines.kokoro_offline import TTS

        with httpx.Client(trust_env=False, follow_redirects=False) as client:
            brain = Brain(settings, client, make_stt(settings), TTS(settings))
            with HTTPServer((args.host, args.port), handler_for(brain, token)) as server:
                print(f"Iris droid server listening on {args.host}:{args.port}")
                server.serve_forever()
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, ImportError) as error:
        print(f"Droid server setup: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
