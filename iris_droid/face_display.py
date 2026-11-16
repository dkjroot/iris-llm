"""Serve the droid's optional display from this client, on loopback only."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from threading import Lock, Thread
from time import monotonic
from typing import Literal, cast, get_args

from iris_droid.expression import FaceExpression

FaceHead = Literal["humanoid-android", "sprout", "signal", "rivet"]
FaceIdle = Literal["snow", "black"]
FaceActivity = Literal["idle", "listening", "thinking", "speaking"]
FACE_HEADS: tuple[FaceHead, ...] = get_args(FaceHead)
FACE_IDLE_MODES: tuple[FaceIdle, ...] = get_args(FaceIdle)
DEFAULT_FACE_PORT = 8767

ASSETS = {
    "/": ("face.html", "text/html; charset=utf-8"),
    "/face.css": ("face.css", "text/css; charset=utf-8"),
    "/face.js": ("face.js", "text/javascript; charset=utf-8"),
    "/face-rig.js": ("face-rig.js", "text/javascript; charset=utf-8"),
    "/face-portraits.js": ("face-portraits.js", "text/javascript; charset=utf-8"),
    "/face-stylised.js": ("face-stylised.js", "text/javascript; charset=utf-8"),
}


class FaceDisplay:
    def __init__(self, head: FaceHead | None, screensaver: FaceIdle = "snow") -> None:
        self.head = head
        self.screensaver = screensaver
        self._lock = Lock()
        self._activity: FaceActivity = "idle"
        self._expression: FaceExpression = "neutral"
        self._deadline: float | None = None

    def show(
        self,
        activity: FaceActivity,
        expression: FaceExpression | None = None,
        *,
        seconds: float | None = None,
    ) -> None:
        if self.head is None:
            return
        with self._lock:
            self._activity = activity
            self._expression = expression or "neutral"
            self._deadline = None if seconds is None else monotonic() + seconds

    @contextmanager
    def speaking(self, expression: FaceExpression | None) -> Iterator[None]:
        self.show("speaking", expression)
        try:
            yield
        finally:
            self.show("idle")

    def state(self) -> bytes:
        with self._lock:
            expired = self._deadline is not None and monotonic() >= self._deadline
            return json.dumps(
                {
                    "head": self.head,
                    "screensaver": self.screensaver,
                    "activity": "idle" if expired else self._activity,
                    "expression": "neutral" if expired else self._expression,
                }
            ).encode()

    @contextmanager
    def serve(self, port: int = DEFAULT_FACE_PORT) -> Iterator[int | None]:
        if self.head is None:
            yield None
            return
        with ThreadingHTTPServer(("127.0.0.1", port), self._handler()) as server:
            worker = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
            worker.start()
            try:
                yield server.server_port
            finally:
                server.shutdown()
                worker.join()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        display = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                self.request.settimeout(5)
                super().setup()

            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                port = cast(ThreadingHTTPServer, self.server).server_port
                expected_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
                if self.headers.get("Host") not in expected_hosts:
                    self.send_error(400)
                    return
                if self.path == "/state":
                    body = display.state()
                    content_type = "application/json"
                elif self.path in ASSETS:
                    name, content_type = ASSETS[self.path]
                    body = files("iris_droid").joinpath("web", name).read_bytes()
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; frame-ancestors 'none'; object-src 'none'",
                )
                self.end_headers()
                with suppress(BrokenPipeError, ConnectionResetError):
                    self.wfile.write(body)

        return Handler
