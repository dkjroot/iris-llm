"""Serve the droid's optional display from this client, on loopback only."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from tempfile import NamedTemporaryFile
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
FACE_PREVIEW_SECONDS = 4
LOGGER = logging.getLogger(__name__)


def next_face(head: FaceHead) -> FaceHead:
    return FACE_HEADS[(FACE_HEADS.index(head) + 1) % len(FACE_HEADS)]


BROWSER_FACE_ASSETS = {
    "/face.css": ("face.css", "text/css; charset=utf-8"),
    "/face.js": ("face.js", "text/javascript; charset=utf-8"),
    "/face-rig.js": ("face-rig.js", "text/javascript; charset=utf-8"),
    "/face-portraits.js": ("face-portraits.js", "text/javascript; charset=utf-8"),
    "/face-stylised.js": ("face-stylised.js", "text/javascript; charset=utf-8"),
    "/menu.js": ("menu.js", "text/javascript; charset=utf-8"),
    "/commands.json": ("commands.json", "application/json"),
}
ASSETS = {
    **BROWSER_FACE_ASSETS,
    "/": ("face.html", "text/html; charset=utf-8"),
    "/face-page.js": ("face-page.js", "text/javascript; charset=utf-8"),
}


class FaceDisplay:
    def __init__(
        self, head: FaceHead | None, screensaver: FaceIdle = "snow", *, remember: bool = False
    ) -> None:
        self._state_path: Path | None = None
        if head is not None and remember:
            state_home = Path(os.environ.get("XDG_STATE_HOME", ""))
            if not state_home.is_absolute():
                state_home = Path.home() / ".local" / "state"
            self._state_path = state_home / "iris" / "face"
            try:
                saved = self._state_path.read_text(encoding="utf-8").strip()
                if saved in FACE_HEADS:
                    head = saved
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as error:
                LOGGER.warning("Could not restore the selected face: %s", error)
        self.head = head
        self.screensaver = screensaver
        self.personalities: tuple[str, ...] = ()
        self.active_personality: Callable[[], str | None] = lambda: None
        self.select_personality: Callable[[str], bool] = lambda _: False
        self.wake_phrase: str | None = None
        self._lock = Lock()
        self._activity: FaceActivity = "idle"
        self._expression: FaceExpression = "neutral"
        self._deadline: float | None = None
        self._preview_until = 0.0

    def cycle_face(self) -> bool:
        with self._lock:
            if self.head is None:
                return False
            self.head = next_face(self.head)
            self._preview_until = monotonic() + FACE_PREVIEW_SECONDS
            self._save_face(self.head)
            return True

    def _save_face(self, head: FaceHead) -> None:
        path = self._state_path
        if path is None:
            return
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(head + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
        except OSError as error:
            LOGGER.warning("Could not save the selected face: %s", error)
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)

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
            now = monotonic()
            expired = self._deadline is not None and now >= self._deadline
            state: dict[str, object] = {
                "head": self.head,
                "screensaver": self.screensaver,
                "activity": "idle" if expired else self._activity,
                "expression": "neutral" if expired else self._expression,
            }
            if now < self._preview_until:
                state["preview"] = True
        if self.personalities:
            state.update(personalities=self.personalities, personality=self.active_personality())
        if self.wake_phrase:
            state["wake_phrase"] = self.wake_phrase
        return json.dumps(state, ensure_ascii=False).encode()

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

            def valid_host(self) -> bool:
                port = cast(ThreadingHTTPServer, self.server).server_port
                return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

            def do_GET(self) -> None:
                if not self.valid_host():
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

            def do_POST(self) -> None:
                if not self.valid_host():
                    self.send_error(400)
                    return
                if self.path not in {"/personality", "/cycle-face"}:
                    self.send_error(404)
                    return
                if self.headers.get("Origin") not in (None, f"http://{self.headers['Host']}"):
                    self.send_error(403)
                    return
                if self.headers.get_content_type() != "application/json":
                    self.send_error(415)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if self.headers.get("Transfer-Encoding") or not 0 < length <= 512:
                        raise ValueError
                    value: object = json.loads(self.rfile.read(length))
                except (OSError, ValueError):
                    self.send_error(400)
                    return
                if self.path == "/cycle-face":
                    accepted = value is None and display.cycle_face()
                else:
                    accepted = isinstance(value, str) and display.select_personality(value)
                if not accepted:
                    self.send_error(400)
                    return
                self.send_response(204)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()

        return Handler
