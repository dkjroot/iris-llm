"""Bound connection threads and serialize admitted turns in arrival order."""

from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socket import socket
from threading import BoundedSemaphore, Condition
from time import monotonic

from iris_turn import check_deadline, remaining_timeout


class QueueTimeout(Exception):
    """The brain did not become available within the queue budget."""


class TurnQueue:
    def __init__(self) -> None:
        self.condition = Condition()
        self.waiters: deque[object] = deque()

    @contextmanager
    def enter(self, timeout: float) -> Iterator[None]:
        ticket = object()
        expires = monotonic() + remaining_timeout(timeout)
        with self.condition:
            self.waiters.append(ticket)
            try:
                while self.waiters[0] is not ticket:
                    check_deadline()
                    wait = expires - monotonic()
                    if wait <= 0:
                        raise QueueTimeout()
                    self.condition.wait(wait)
                check_deadline()
            except BaseException:
                self.waiters.remove(ticket)
                self.condition.notify_all()
                raise
        try:
            yield
        finally:
            with self.condition:
                self.waiters.remove(ticket)
                self.condition.notify_all()


class BoundedHTTPServer(ThreadingHTTPServer):
    """Cap pre-authentication/header threads as well as admitted turn bodies.

    The handler separately caps uploads/queued turns. Spare connection slots let
    cancellation decisions bypass a busy brain. No request is retried here.
    """

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        bind_and_activate: bool = True,
    ) -> None:
        super().__init__(address, handler, bind_and_activate)
        self._connections = BoundedSemaphore(64)

    def process_request(
        self, request: socket | tuple[bytes, socket], client_address: tuple[str, int]
    ) -> None:
        assert isinstance(request, socket)  # HTTP uses TCP, never datagram requests.
        if not self._connections.acquire(blocking=False):
            try:
                request.settimeout(0.1)
                request.sendall(
                    b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._connections.release()
            raise

    def process_request_thread(
        self, request: socket | tuple[bytes, socket], client_address: tuple[str, int]
    ) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()
