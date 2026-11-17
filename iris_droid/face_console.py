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
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from functools import lru_cache
from importlib import import_module
from importlib.resources import files
from threading import Event, Lock, Thread
from time import monotonic
from types import FrameType, ModuleType
from typing import Any, Protocol, TypedDict, cast, get_args

from iris_droid.console_menu import ConsoleMenu
from iris_droid.expression import FACE_EXPRESSIONS, FaceExpression
from iris_droid.face_display import (
    DEFAULT_FACE_PORT,
    FACE_HEADS,
    FACE_IDLE_MODES,
    FACE_PREVIEW_SECONDS,
    FaceActivity,
    FaceHead,
    FaceIdle,
    next_face,
)
from iris_droid.speech_motion import read_mouth, sample_mouth

ACTIVITIES: tuple[FaceActivity, ...] = get_args(FaceActivity)


@dataclass(frozen=True)
class DisplayState:
    head: FaceHead
    activity: FaceActivity
    expression: FaceExpression
    screensaver: FaceIdle
    personalities: tuple[str, ...] = ()
    personality: str | None = None
    wake_phrase: str | None = None
    preview: bool = False
    show_idle: bool = False
    mouth: tuple[tuple[float, float], ...] | None = None
    notice: tuple[str, str, str] | None = None
    companion_mode: bool | None = None
    commands_ready: bool = False


def read_state(port: int) -> DisplayState | None:
    received = monotonic()
    link = http.client.HTTPConnection("127.0.0.1", port, timeout=0.35)
    try:
        link.request("GET", "/state")
        response = link.getresponse()
        if response.status != 200:
            return None
        value: object = json.loads(response.read(16384))
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
    names = state.get("personalities")
    personalities = (
        tuple(name for name in names if isinstance(name, str)) if isinstance(names, list) else ()
    )
    notice = state.get("notice")
    visible_notice = None
    if isinstance(notice, dict):
        title, message, status = notice.get("title"), notice.get("message"), notice.get("status")
        if (
            isinstance(title, str)
            and len(title) <= 80
            and isinstance(message, str)
            and len(message) <= 320
            and isinstance(status, str)
            and status in {"success", "error", "info", "processing"}
        ):
            visible_notice = (title, message, str(status))
    companion = state.get("companion_mode")
    active = state.get("personality")
    wake = state.get("wake_phrase")
    return DisplayState(
        head,
        activity,
        expression if expression in FACE_EXPRESSIONS else "neutral",
        idle if idle in FACE_IDLE_MODES else "black",
        personalities,
        active if isinstance(active, str) and active in personalities else None,
        wake if isinstance(wake, str) else None,
        preview=state.get("preview") is True,
        show_idle=state.get("show_idle") is True,
        mouth=read_mouth(state["mouth"], received) if "mouth" in state else None,
        notice=visible_notice,
        companion_mode=companion if isinstance(companion, bool) else None,
        commands_ready=state.get("commands_ready") is True,
    )


def request_personality(port: int, name: str) -> bool:
    return post_control(port, "/personality", name)


def request_command(port: int, phrase: str) -> bool:
    return post_control(port, "/command", phrase)


def request_face_cycle(port: int) -> bool:
    return post_control(port, "/cycle-face", None)


def post_control(port: int, path: str, value: str | None) -> bool:
    link = http.client.HTTPConnection("127.0.0.1", port, timeout=0.7)
    try:
        link.request(
            "POST",
            path,
            json.dumps(value, ensure_ascii=False).encode(),
            {"Content-Type": "application/json"},
        )
        return link.getresponse().status == 204
    except (OSError, http.client.HTTPException):
        return False
    finally:
        link.close()


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
            stopped.wait(0.05 if value is not None and value.activity == "speaking" else 0.2)

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
    cycle = seconds % 13.7
    closure = 0.0
    for start in (0, 4.63, 9.8, 10.12):
        t = (cycle - start) / 0.19
        if 0 <= t <= 1:
            closure = math.sin(math.pi * t)
    eyes = "rest"
    if closure >= 0.3:
        eyes = "blink" if closure > 0.72 else "half-blink"
    elif state.activity == "thinking":
        eyes = "thinking"
    elif state.activity != "speaking":
        eyes = ("rest", "look-left", "rest", "look-right", "rest")[int(seconds / 2.7) % 5]
    if state.activity == "speaking":
        # Demo states have no envelope; live playback includes explicit silence.
        jaw = (
            sample_mouth(state.mouth, seconds)
            if state.mouth is not None
            else max(0, math.sin(seconds * 18 + math.sin(seconds * 9))) ** 1.4
        )
        mouth = ("rest", "jaw-small", "jaw-medium", "jaw-wide")[round(jaw * 3)]
        if eyes in ("blink", "half-blink") and mouth != "rest":
            return expression, f"{eyes}|{mouth}"
        return expression, eyes if mouth == "rest" else mouth
    return expression, eyes


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
    def fill(self, color: tuple[int, int, int] | tuple[int, int, int, int]) -> object: ...
    def blit(self, source: Surface, dest: tuple[int, int]) -> object: ...
    def subsurface(self, rect: tuple[int, int, int, int]) -> Surface: ...
    def convert_alpha(self) -> Surface: ...
    def copy(self) -> Surface: ...


class SpriteManifest(TypedDict):
    size: list[int]
    crop: list[int]
    expressions: list[FaceExpression]
    frames: list[str]
    mouthSplit: int
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

    @lru_cache(maxsize=16)
    def frame(expression: FaceExpression, name: str) -> Surface:
        eyes, _, mouth = name.partition("|")
        col = manifest["frames"].index(eyes)
        row = manifest["expressions"].index(expression)
        pixels = atlas.subsurface((tile_w * col, height + tile_h * row, tile_w, tile_h))
        if mouth:
            # Compose lids and jaw independently, without a combinatorial atlas.
            pixels = pixels.copy()
            split = manifest["mouthSplit"]
            mouth_col = manifest["frames"].index(mouth)
            lower = atlas.subsurface(
                (tile_w * mouth_col, height + tile_h * row + split, tile_w, tile_h - split)
            )
            # Clear first: alpha-blitting a mouth over another leaves ghost lips.
            pixels.subsurface((0, split, tile_w, tile_h - split)).fill((0, 0, 0, 0))
            pixels.blit(lower, (0, split))
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
        speed = (6 + (i * 3) % 11) * 1.25
        y = (height * ((i * 53 + 23) % 97) / 97 + seconds * speed) % height
        size = 3 if i % 3 == 0 else 2
        pygame.draw.rect(screen, (25, 31, 36), (round(x), round(y), size, size))


def smile_icon(pygame: ModuleType, size: int) -> Surface:
    # Draw large and scale down for smooth curves on the panel.
    icon = pygame.Surface((72, 72), pygame.SRCALPHA)
    ink = (249, 246, 255)
    pygame.draw.circle(icon, ink, (36, 36), 33, width=5)
    pygame.draw.circle(icon, ink, (27, 29), 4)
    pygame.draw.circle(icon, ink, (45, 29), 4)
    pygame.draw.arc(icon, ink, (20, 20, 32, 34), 1.15 * math.pi, 1.85 * math.pi, width=5)
    return cast(Surface, pygame.transform.smoothscale(icon, (size, size)))


def render(
    pygame: ModuleType,
    screen: Surface,
    current: Callable[[], DisplayState | None],
    select: Callable[[str], Future[bool]],
    cycle_face: Callable[[], None],
    execute: Callable[[str], Future[bool]] | None = None,
) -> None:
    clock = pygame.time.Clock()
    head: FaceHead | None = None
    sprites: Callable[[FaceExpression, str], Surface] | None = None
    previous_blank = False
    menu = ConsoleMenu(pygame, screen, select, execute)
    cycle_button = pygame.Rect(menu.px(12), menu.px(12), menu.px(50), menu.px(50))
    icon_size = menu.px(26)
    cycle_icon = smile_icon(pygame, icon_size)
    while True:
        seconds = monotonic()
        state = current()
        visible_face = state is not None and (
            state.preview or state.show_idle or state.activity != "idle"
        )
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                if menu.page is None:
                    return
                menu.go(None)
            if (position := tap_position(pygame, event, screen.get_size())) is not None:
                if menu.page is None and visible_face and cycle_button.collidepoint(position):
                    cycle_face()
                else:
                    menu.tap(*position, seconds)
        if menu.draw(state, seconds):
            previous_blank = False
            clock.tick(24)
            continue
        black = not visible_face and (
            state is None or (state.screensaver == "black" and state.notice is None)
        )
        if not black or not previous_blank:
            screen.fill((0, 0, 0))
            if state is not None:
                if visible_face:
                    width, height = screen.get_size()
                    if sprites is None or head != state.head:
                        head = state.head
                        sprites = load_sprites(pygame, head, (width, height))
                    sprite = sprites(*frame_for(state, seconds))
                    sw, sh = sprite.get_size()
                    center = (width - sw) // 2, (height - sh) // 2 + round(math.sin(seconds * 1.4))
                    screen.blit(sprite, center)
                    pygame.draw.rect(screen, (25, 22, 42), cycle_button, border_radius=menu.px(14))
                    pygame.draw.rect(
                        screen, (83, 78, 109), cycle_button, width=1, border_radius=menu.px(14)
                    )
                    screen.blit(
                        cycle_icon,
                        (
                            cycle_button.centerx - icon_size // 2,
                            cycle_button.centery - icon_size // 2,
                        ),
                    )
                elif state.screensaver == "snow":
                    draw_snow(pygame, screen, seconds)
            menu.draw_notice(state)
            pygame.display.flip()
        previous_blank = black
        clock.tick(12 if state is None or state.activity == "idle" else 24)


def tap_position(pygame: ModuleType, event: Any, size: tuple[int, int]) -> tuple[int, int] | None:
    if event.type == pygame.FINGERUP:
        width, height = size
        return (
            min(width - 1, max(0, int(event.x * width))),
            min(height - 1, max(0, int(event.y * height))),
        )
    if (
        event.type == pygame.MOUSEBUTTONUP
        and event.button == 1
        and not getattr(event, "touch", False)
    ):
        return int(event.pos[0]), int(event.pos[1])
    return None


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
    demo_head: FaceHead | None = args.demo
    demo_started = monotonic()
    demo_preview_until = 0.0

    def demo_current() -> DisplayState:
        assert demo_head is not None
        return replace(
            demo_state(demo_head, demo_started), preview=monotonic() < demo_preview_until
        )

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
                feed = nullcontext(demo_current) if demo_head is not None else live_feed(args.port)
                with feed as current, ThreadPoolExecutor(max_workers=1) as worker:
                    cycle_request: Future[bool] | None = None

                    def cycle_face() -> None:
                        nonlocal cycle_request, demo_head, demo_preview_until
                        if demo_head is not None:
                            demo_head = next_face(demo_head)
                            demo_preview_until = monotonic() + FACE_PREVIEW_SECONDS
                        elif cycle_request is None or cycle_request.done():
                            cycle_request = worker.submit(request_face_cycle, args.port)

                    render(
                        pygame,
                        screen,
                        current,
                        lambda name: worker.submit(request_personality, args.port, name),
                        cycle_face,
                        (lambda phrase: worker.submit(request_command, args.port, phrase))
                        if demo_head is None
                        else None,
                    )
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
