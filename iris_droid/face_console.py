"""Draw Iris on a Linux KMS display, or in an SDL window for testing."""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import signal
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from functools import lru_cache, partial
from importlib import import_module
from importlib.resources import files
from threading import Event, Lock, Thread
from time import monotonic
from types import FrameType, ModuleType
from typing import Protocol, TypedDict, cast, get_args

from iris_droid.expression import FACE_EXPRESSIONS, FaceExpression
from iris_droid.face_display import (
    DEFAULT_FACE_PORT,
    FACE_HEADS,
    FACE_IDLE_MODES,
    FaceActivity,
    FaceHead,
    FaceIdle,
)

ACTIVITIES: tuple[FaceActivity, ...] = get_args(FaceActivity)


@dataclass(frozen=True)
class DisplayState:
    head: FaceHead
    activity: FaceActivity
    expression: FaceExpression
    screensaver: FaceIdle


def read_state(port: int) -> DisplayState | None:
    link = http.client.HTTPConnection("127.0.0.1", port, timeout=0.35)
    try:
        link.request("GET", "/state")
        response = link.getresponse()
        if response.status != 200:
            return None
        value: object = json.loads(response.read(2048))
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        link.close()
    if not isinstance(value, dict):
        return None
    state = cast(dict[str, object], value)
    head, activity = state.get("head"), state.get("activity")
    expression, idle = state.get("expression"), state.get("screensaver")
    if head not in FACE_HEADS or activity not in ACTIVITIES:
        return None
    return DisplayState(
        head,
        activity,
        expression if expression in FACE_EXPRESSIONS else "neutral",
        idle if idle in FACE_IDLE_MODES else "black",
    )


@contextmanager
def live_feed(port: int) -> Iterator[Callable[[], DisplayState | None]]:
    # A client restart or slow socket should never freeze the display loop.
    lock = Lock()
    stopped = Event()
    latest: DisplayState | None = None

    def collect() -> None:
        nonlocal latest
        while not stopped.is_set():
            value = read_state(port)
            with lock:
                latest = value
            stopped.wait(0.2)

    def current() -> DisplayState | None:
        with lock:
            return latest

    worker = Thread(target=collect, name="iris-face-state", daemon=True)
    worker.start()
    try:
        yield current
    finally:
        stopped.set()
        worker.join()


def demo_state(head: FaceHead, started: float) -> DisplayState:
    step = (monotonic() - started) % 19
    if step < 4:
        return DisplayState(head, "listening", "curious", "snow")
    if step < 7:
        return DisplayState(head, "thinking", "neutral", "snow")
    if step < 13:
        return DisplayState(head, "speaking", "amused", "snow")
    return DisplayState(head, "idle", "neutral", "snow")


def frame_for(state: DisplayState, seconds: float) -> tuple[FaceExpression, str]:
    expression = "focused" if state.activity == "thinking" else state.expression
    blink = seconds % 4.63
    if blink < 0.19:
        closure = math.sin(math.pi * blink / 0.19)
        if closure >= 0.3:
            return expression, "blink" if closure > 0.72 else "half-blink"
    if state.activity == "thinking":
        return expression, "thinking"
    if state.activity == "speaking":
        jaw = max(0, math.sin(seconds * 18 + math.sin(seconds * 9))) ** 1.4
        return expression, ("rest", "jaw-small", "jaw-medium", "jaw-wide")[round(jaw * 3)]
    return expression, "rest"


def fit_size(source: tuple[int, int], bounds: tuple[int, int]) -> tuple[int, int]:
    ratio = min(bounds[0] / source[0], bounds[1] / source[1])
    return max(1, round(source[0] * ratio)), max(1, round(source[1] * ratio))


def window_size(value: str) -> tuple[int, int]:
    width, _, height = value.lower().partition("x")
    try:
        size = int(width), int(height)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use WIDTHxHEIGHT, for example 1024x600.") from error
    if min(size) <= 0:
        raise argparse.ArgumentTypeError("Width and height must both be positive.")
    return size


class Surface(Protocol):
    """The small part of the optional pygame surface API we use."""

    def get_size(self) -> tuple[int, int]: ...
    def fill(self, color: tuple[int, int, int]) -> object: ...
    def blit(self, source: Surface, dest: tuple[int, int]) -> object: ...
    def subsurface(self, rect: tuple[int, int, int, int]) -> Surface: ...
    def convert_alpha(self) -> Surface: ...
    def copy(self) -> Surface: ...


class SpriteManifest(TypedDict):
    size: list[int]
    crop: list[int]
    expressions: list[FaceExpression]
    frames: list[str]
    tilts: dict[FaceHead, dict[FaceExpression, float]]


def load_sprites(
    pygame: ModuleType, head: FaceHead, screen_size: tuple[int, int]
) -> Callable[[FaceExpression, str], Surface]:
    folder = files("iris_droid").joinpath("face_sprites")
    manifest = cast(SpriteManifest, json.loads(folder.joinpath("sprites.json").read_text("utf-8")))
    with folder.joinpath(f"{head}.png").open("rb") as asset:
        atlas = cast(Surface, pygame.image.load(asset, f"{head}.png")).convert_alpha()
    width, height = manifest["size"]
    x, y, tile_w, tile_h = manifest["crop"]
    target_w, target_h = fit_size((width, height), screen_size)
    sx, sy = target_w / width, target_h / height
    base = cast(
        Surface,
        pygame.transform.smoothscale(atlas.subsurface((0, 0, width, height)), (target_w, target_h)),
    )
    position = round(x * sx), round(y * sy)
    feature_size = round(tile_w * sx), round(tile_h * sy)

    @lru_cache(maxsize=10)
    def frame(expression: FaceExpression, name: str) -> Surface:
        col = manifest["frames"].index(name)
        row = manifest["expressions"].index(expression)
        pixels = atlas.subsurface((tile_w * col, height + tile_h * row, tile_w, tile_h))
        features = cast(Surface, pygame.transform.smoothscale(pixels, feature_size))
        combined = base.copy()
        combined.blit(features, position)
        angle = manifest["tilts"][head][expression]
        if angle:
            return cast(Surface, pygame.transform.rotozoom(combined, angle, 1.0))
        return combined

    return frame


def draw_snow(pygame: ModuleType, screen: Surface, seconds: float) -> None:
    width, height = screen.get_size()
    for i in range(9):
        x = width * ((i * 29 + 7) % 101) / 101 + math.sin(seconds * 0.35 + i) * 4
        y = (height * ((i * 53 + 23) % 97) / 97 + seconds * (6 + (i * 3) % 11)) % height
        size = 2 if i % 3 == 0 else 1
        pygame.draw.rect(screen, (25, 31, 36), (round(x), round(y), size, size))


def render(
    pygame: ModuleType,
    screen: Surface,
    current: Callable[[], DisplayState | None],
) -> None:
    clock = pygame.time.Clock()
    head: FaceHead | None = None
    sprites: Callable[[FaceExpression, str], Surface] | None = None
    previous_black = False
    while not any(
        event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE)
        for event in pygame.event.get()
    ):
        state = current()
        seconds = monotonic()
        black = state is None or (state.activity == "idle" and state.screensaver == "black")
        if not black or not previous_black:
            screen.fill((0, 0, 0))
            if state is not None:
                if state.activity == "idle":
                    if state.screensaver == "snow":
                        draw_snow(pygame, screen, seconds)
                else:
                    width, height = screen.get_size()
                    if sprites is None or head != state.head:
                        head = state.head
                        sprites = load_sprites(pygame, head, (width, height))
                    sprite = sprites(*frame_for(state, seconds))
                    sw, sh = sprite.get_size()
                    center = (width - sw) // 2, (height - sh) // 2 + round(math.sin(seconds * 1.4))
                    screen.blit(sprite, center)
            pygame.display.flip()
        previous_black = black
        clock.tick(12 if state is None or state.activity == "idle" else 24)


@contextmanager
def graphics_console(enabled: bool) -> Iterator[None]:
    # systemd passes tty1 as stdin. From SSH there is no local VT to silence.
    if sys.platform != "linux":
        yield
        return
    if not enabled or not sys.stdin.isatty():
        yield
        return
    fd = sys.stdin.fileno()
    tty = os.ttyname(fd)
    if not tty.startswith("/dev/tty") or not tty[len("/dev/tty") :].isdigit():
        yield
        return
    ioctl = cast(Callable[[int, int, int], int], import_module("fcntl").ioctl)
    kd_set_mode = 0x4B3A
    ioctl(fd, kd_set_mode, 1)  # KD_GRAPHICS
    try:
        yield
    finally:
        ioctl(fd, kd_set_mode, 0)  # KD_TEXT


def _stop(_signal: int, _frame: FrameType | None) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", type=int, default=DEFAULT_FACE_PORT, help="Local client face port"
    )
    parser.add_argument("--demo", choices=FACE_HEADS, help="Play a sample without the voice client")
    parser.add_argument(
        "--window",
        nargs="?",
        const=(800, 480),
        type=window_size,
        metavar="WIDTHxHEIGHT",
        help="Test in a desktop window (800 × 480 if no size is given)",
    )
    parser.add_argument("--kms-card", type=int, help="Use this /dev/dri/card number in direct mode")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535 or (args.kms_card is not None and not 0 <= args.kms_card <= 99):
        parser.error("Pass a valid local port and a KMS card number from 0 to 99.")
    if not args.window and sys.platform != "linux":
        parser.error("Direct display requires Linux. Use --window for desktop testing.")

    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    if not args.window:
        os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
        os.environ["SDL_KMSDRM_REQUIRE_DRM_MASTER"] = "1"
        if args.kms_card is not None:
            os.environ["SDL_KMSDRM_DEVICE_INDEX"] = str(args.kms_card)
    try:
        pygame = import_module("pygame")
    except ImportError:
        print(
            "Install python3-pygame and run this with the Pi's /usr/bin/python3.", file=sys.stderr
        )
        return 1

    previous = signal.signal(signal.SIGTERM, _stop)
    try:
        with graphics_console(not args.window):
            pygame.display.init()
            try:
                pygame.display.set_caption("Iris face")
                # In fullscreen, SDL uses the active KMS mode for the connected panel.
                size = args.window or (0, 0)
                flags = 0 if args.window else pygame.FULLSCREEN
                screen = cast(Surface, pygame.display.set_mode(size, flags))
                pygame.mouse.set_visible(False)
                width, height = screen.get_size()
                print(
                    f"Iris display: {pygame.display.get_driver()}, {width} × {height}", flush=True
                )
                feed = (
                    nullcontext(partial(demo_state, args.demo, monotonic()))
                    if args.demo
                    else live_feed(args.port)
                )
                with feed as current:
                    render(pygame, screen, current)
            finally:
                pygame.display.quit()
    except KeyboardInterrupt:
        return 0
    except (OSError, pygame.error, ValueError, KeyError) as error:
        print(f"Iris display: {error}", file=sys.stderr)
        if not args.window:
            print(
                "Check the KMS display, video/render access, and desktop status.", file=sys.stderr
            )
        return 1
    finally:
        signal.signal(signal.SIGTERM, previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
