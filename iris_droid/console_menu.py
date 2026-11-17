"""Touch menus for the direct SDL display. Uses the system Python and Pygame."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import Future
from functools import partial
from importlib.resources import files
from types import ModuleType
from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict, cast

if TYPE_CHECKING:
    from iris_droid.face_console import DisplayState, Surface

Color = tuple[int, int, int]
Rect = tuple[int, int, int, int]
Phrase = tuple[str, str]

BACKGROUND = (23, 19, 41)
PANEL = (44, 36, 66)
INK = (34, 24, 44)
WHITE = (252, 247, 255)
MUTED = (222, 208, 236)
MINT = (152, 235, 203)
LILAC = (214, 180, 255)
PEACH = (255, 202, 157)
SKY = (165, 220, 255)
COLORS = {"mint": MINT, "lilac": LILAC, "peach": PEACH, "sky": SKY}


class Category(TypedDict):
    title: str
    color: str
    note: NotRequired[str]
    commands: list[Phrase]


class TapCommand(TypedDict):
    targets: list[str]
    confirm: bool


class Commands(TypedDict):
    categories: list[Category]
    home: list[Phrase]
    tap_commands: NotRequired[dict[str, TapCommand]]


class Font(Protocol):
    def size(self, text: str) -> tuple[int, int]: ...
    def get_linesize(self) -> int: ...
    def render(self, text: str, antialias: bool, color: Color) -> Surface: ...


class ConsoleMenu:
    def __init__(
        self,
        pygame: ModuleType,
        screen: Surface,
        select: Callable[[str], Future[bool]],
        execute: Callable[[str], Future[bool]] | None = None,
    ) -> None:
        self.pygame = pygame
        self.screen = screen
        self.select = select
        self.execute = execute
        self.command_request: Future[bool] | None = None
        self.command_ready = False
        self.confirm_command: tuple[str, str] | None = None
        pygame.font.init()
        self.fonts: dict[tuple[int, bool], Font] = {}
        self.commands = cast(
            Commands,
            json.loads(files("iris_droid").joinpath("web", "commands.json").read_text("utf-8")),
        )
        self.page: str | None = None
        self.category = self.commands["categories"][0]
        self.offset = 0
        self.deadline = 0.0
        self.buttons: list[tuple[Rect, Callable[[], None]]] = []
        self.selection: Future[bool] | None = None
        self.error = ""
        self.last_view: tuple[object, ...] = ()
        self.width, self.height = screen.get_size()
        self.scale = min(self.width / 900, self.height / 510)
        self.margin = self.px(24)
        self.gap = self.px(14)

    def px(self, size: int) -> int:
        return max(1, round(size * self.scale))

    def go(self, page: str | None) -> None:
        self.page = page
        self.offset = 0
        self.buttons.clear()
        self.last_view = ()
        self.error = ""

    def tap(self, x: int, y: int, now: float) -> None:
        self.deadline = now + 180
        if self.page is None:
            self.go("main")
            return
        for (left, top, width, height), action in self.buttons:
            if left <= x < left + width and top <= y < top + height:
                action()
                return

    def draw(self, state: DisplayState | None, now: float) -> bool:
        if self.selection and self.selection.done():
            if not self.selection.result():
                self.error = "Couldn’t switch personality. Try again."
            self.selection = None
        if self.command_request is not None and self.command_request.done():
            if not self.command_request.result():
                self.error = "Command not accepted. Wait for Iris, then try again."
            self.command_request = None
        self.command_ready = bool(state and state.commands_ready and self.execute is not None)
        if self.page is None:
            return False
        if now >= self.deadline:
            self.go(None)
            return False

        names = state.personalities if state else ()
        active = state.personality if state else None
        wake = state.wake_phrase if state else None
        view = (
            self.page,
            self.offset,
            state is not None,
            names,
            active,
            wake,
            self.selection,
            self.error,
            state.notice if state else None,
            self.command_request,
            self.command_ready,
            self.confirm_command,
        )
        if view == self.last_view:
            return True
        self.last_view = view
        self.buttons.clear()
        self.screen.fill(BACKGROUND)
        titles = {
            "main": "Iris menu",
            "commands": "Command list",
            "category": self.category["title"],
            "personalities": "Personalities",
            "home": "Home",
            "confirm-command": "Confirm command",
        }
        self.header(titles[self.page])
        top = self.px(100)
        if self.page == "confirm-command" and self.confirm_command is not None:
            phrase, meaning = self.confirm_command
            top = self.note(f"{phrase}? {meaning}", top)
            if self.error:
                top = self.note(self.error, top)
            choices = [("Go back", LILAC, lambda: self.go("category"))]
            if self.command_ready and self.command_request is None:
                choices.insert(0, (f"Confirm: {phrase}", PEACH, partial(self.run_command, phrase)))
            else:
                top = self.note("Wait for Iris to finish this turn.", top)
            self.tiles(choices, top)
        elif self.page == "main":
            self.tiles(
                [
                    ("Command list", MINT, lambda: self.go("commands")),
                    ("Personalities", LILAC, lambda: self.go("personalities")),
                    ("Home", PEACH, lambda: self.go("home")),
                ],
                top,
            )
        elif self.page == "commands":
            top = self.note("Tap an outlined command, or say any of these phrases aloud.", top)
            self.tiles(
                [
                    (item["title"], COLORS[item["color"]], partial(self.open_category, item))
                    for item in self.commands["categories"]
                ],
                top,
            )
        elif self.page == "category":
            top = self.note(
                self.error
                or (
                    "Wait for Iris to finish this turn."
                    if not self.command_ready
                    else "Tap an outlined command to run it."
                ),
                top,
            )
            if note := self.category.get("note"):
                top = self.note(note, top)
            rows = self.category["commands"]
            if wake and self.category["title"] == "Conversation":
                rows = [(wake, "Wake Iris and wait for the listening cue."), *rows]
            self.phrases(rows, top)
        elif self.page == "personalities":
            if state is None:
                self.note("Waiting for the Iris client…", top)
            elif not names:
                self.note("There are no personalities configured for this client.", top)
            else:
                top = self.note(
                    self.error
                    or (
                        "Switching personality…"
                        if self.selection
                        else "Tap a personality to use it on the next turn."
                    ),
                    top,
                )
                self.tiles(
                    [
                        (
                            f"{name} ✓" if name == active else name,
                            MINT if name == active else LILAC,
                            partial(self.choose, name),
                        )
                        for name in names
                    ],
                    top,
                )
        else:
            top = self.note("With home automation set up, ask Iris about your own devices.", top)
            self.phrases(self.commands["home"], top)
        self.draw_notice(state)
        self.pygame.display.flip()
        return True

    def draw_notice(self, state: DisplayState | None) -> None:
        if state is None:
            return
        if (
            state.companion_mode
            and self.page is None
            and (state.activity != "idle" or state.preview or state.show_idle)
        ):
            badge = self.font(17, True).render("Companion on", True, MINT)
            width, _ = badge.get_size()
            self.screen.blit(badge, (self.width - width - self.margin, self.px(24)))
        if state.notice is None:
            return
        title, message, status = state.notice
        color = {"success": MINT, "error": PEACH, "processing": SKY}.get(status, LILAC)
        width = min(self.width - self.margin * 2, self.px(680))
        lines = self.wrap(message, width - self.px(36), 18)
        if len(lines) > 3:
            lines = [*lines[:2], lines[2].rstrip(" .") + "…"]
        line_h = self.font(18, False).get_linesize()
        height = self.px(54) + line_h * len(lines)
        x, y = (self.width - width) // 2, self.height - self.margin - height
        self.pygame.draw.rect(self.screen, PANEL, (x, y, width, height), border_radius=self.px(14))
        self.pygame.draw.rect(
            self.screen, color, (x, y, width, height), width=2, border_radius=self.px(14)
        )
        self.screen.blit(
            self.font(21, True).render(title, True, color), (x + self.px(18), y + self.px(12))
        )
        for i, line in enumerate(lines):
            self.screen.blit(
                self.font(18, False).render(line, True, WHITE),
                (x + self.px(18), y + self.px(42) + i * line_h),
            )

    def choose_command(self, phrase: str, meaning: str) -> None:
        if not self.command_ready or self.command_request is not None:
            return
        spec = self.commands.get("tap_commands", {}).get(phrase)
        if spec is None or "client" not in spec["targets"]:
            return
        if spec["confirm"]:
            self.confirm_command = (phrase, meaning)
            self.go("confirm-command")
        else:
            self.run_command(phrase)

    def run_command(self, phrase: str) -> None:
        if self.execute is None or not self.command_ready or self.command_request is not None:
            return
        self.command_request = self.execute(phrase)
        self.confirm_command = None
        self.go("category")

    def open_category(self, item: Category) -> None:
        self.category = item
        self.go("category")

    def choose(self, name: str) -> None:
        if self.selection is None:
            self.error = ""
            self.selection = self.select(name)

    def font(self, size: int, bold: bool) -> Font:
        key = (self.px(size), bold)
        if key not in self.fonts:
            self.fonts[key] = cast(
                Font, self.pygame.font.SysFont("dejavusans,arial", key[0], bold=bold)
            )
        return self.fonts[key]

    def wrap(self, text: str, width: int, size: int, bold: bool = False) -> list[str]:
        font = self.font(size, bold)
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}" if current else word
            if font.size(candidate)[0] <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = ""
            for char in word:
                if current and font.size(current + char)[0] > width:
                    lines.append(current)
                    current = ""
                current += char
        if current:
            lines.append(current)
        return lines

    def label(
        self,
        lines: list[str],
        x: int,
        y: int,
        size: int,
        color: Color,
        *,
        bold: bool = False,
        centered: bool = False,
    ) -> None:
        font = self.font(size, bold)
        for line in lines:
            label = font.render(line, True, color)
            left = x - label.get_size()[0] // 2 if centered else x
            self.screen.blit(label, (left, y))
            y += font.get_linesize()

    def button(
        self, rect: Rect, text: str, color: Color, action: Callable[[], None], size: int = 26
    ) -> None:
        x, y, width, height = rect
        shadow = tuple(round(channel * 0.62) for channel in color)
        self.pygame.draw.rect(
            self.screen,
            shadow,
            (x, y + self.px(4), width, height),
            border_radius=self.px(16),
        )
        self.pygame.draw.rect(self.screen, color, rect, border_radius=self.px(16))
        lines = self.wrap(text, width - self.px(20), size, True)
        spacing = self.font(size, True).get_linesize()
        self.label(
            lines,
            x + width // 2,
            y + (height - len(lines) * spacing) // 2,
            size,
            INK,
            bold=True,
            centered=True,
        )
        self.buttons.append((rect, action))

    def header(self, title: str) -> None:
        left = self.margin
        if self.page != "main":
            destination = (
                "category"
                if self.page == "confirm-command"
                else "commands"
                if self.page == "category"
                else "main"
            )
            self.button(
                (left, self.px(17), self.px(57), self.px(50)),
                "←",
                LILAC,
                lambda: self.go(destination),
                30,
            )
            left += self.px(72)
        self.label([title], left, self.px(23), 32, WHITE, bold=True)
        self.button(
            (self.width - self.margin - self.px(55), self.px(17), self.px(55), self.px(50)),
            "×",
            (247, 180, 181),
            lambda: self.go(None),
            34,
        )
        self.pygame.draw.line(
            self.screen, (64, 54, 80), (0, self.px(83)), (self.width, self.px(83)), self.px(1)
        )

    def note(self, text: str, top: int) -> int:
        lines = self.wrap(text, self.width - 2 * self.margin, 19)
        self.label(lines, self.margin, top, 19, MUTED)
        return top + len(lines) * self.font(19, False).get_linesize() + self.gap

    def move(self, offset: int) -> None:
        self.offset = offset

    def pager(self, page_size: int, total: int) -> None:
        if total <= page_size:
            return
        top = self.height - self.px(64)
        if self.offset:
            self.button(
                (self.margin, top, self.px(134), self.px(44)),
                "‹ Previous",
                SKY,
                lambda: self.move(self.offset - page_size),
                17,
            )
        if self.offset + page_size < total:
            self.button(
                (self.width - self.margin - self.px(134), top, self.px(134), self.px(44)),
                "Next ›",
                SKY,
                lambda: self.move(self.offset + page_size),
                17,
            )
        text = f"{self.offset // page_size + 1} / {(total + page_size - 1) // page_size}"
        self.label([text], self.width // 2, top + self.px(11), 18, MUTED, centered=True)

    def tiles(self, items: list[tuple[str, Color, Callable[[], None]]], top: int) -> None:
        columns = 3 if self.width >= self.height else 2
        width = (self.width - 2 * self.margin - (columns - 1) * self.gap) // columns
        height = self.px(100)
        stride = height + self.gap
        page_size = columns * max(1, (self.height - top - self.px(74)) // stride)
        self.offset = min(self.offset, (len(items) - 1) // page_size * page_size)
        for index, (name, color, action) in enumerate(items[self.offset : self.offset + page_size]):
            row, column = divmod(index, columns)
            self.button(
                (self.margin + column * (width + self.gap), top + row * stride, width, height),
                name,
                color,
                action,
            )
        self.pager(page_size, len(items))

    def phrases(self, rows: list[Phrase], top: int) -> None:
        width = self.width - 2 * self.margin
        padding = self.px(12)
        wide = self.width >= self.height
        term_width = (width - 3 * padding) * 45 // 100 if wide else width - 2 * padding
        meaning_width = width - term_width - 3 * padding if wide else term_width
        terms = [self.wrap(phrase, term_width, 21, True) for phrase, _ in rows]
        meanings = [self.wrap(meaning, meaning_width, 17) for _, meaning in rows]
        term_height = self.font(21, True).get_linesize()
        meaning_height = self.font(17, False).get_linesize()
        heights = [
            max(len(term) * term_height, len(meaning) * meaning_height)
            if wide
            else len(term) * term_height + len(meaning) * meaning_height
            for term, meaning in zip(terms, meanings, strict=True)
        ]
        height = max(heights) + 2 * padding
        stride = height + self.px(8)
        page_size = max(1, (self.height - top - self.px(74)) // stride)
        self.offset = min(self.offset, (len(rows) - 1) // page_size * page_size)
        for index in range(self.offset, min(self.offset + page_size, len(rows))):
            y = top + (index - self.offset) * stride
            self.pygame.draw.rect(
                self.screen, PANEL, (self.margin, y, width, height), border_radius=self.px(13)
            )
            spec = self.commands.get("tap_commands", {}).get(rows[index][0])
            tappable = spec is not None and "client" in spec["targets"] and self.execute is not None
            if tappable:
                color = MINT if self.command_ready and self.command_request is None else MUTED
                self.pygame.draw.rect(
                    self.screen,
                    color,
                    (self.margin, y, width, height),
                    width=2,
                    border_radius=self.px(13),
                )
                if self.command_ready and self.command_request is None:
                    self.buttons.append(
                        (
                            (self.margin, y, width, height),
                            partial(self.choose_command, *rows[index]),
                        )
                    )
            self.label(terms[index], self.margin + padding, y + padding, 21, MINT, bold=True)
            self.label(
                meanings[index],
                self.margin + (2 * padding + term_width if wide else padding),
                y + padding + (0 if wide else len(terms[index]) * term_height),
                17,
                WHITE,
            )
        self.pager(page_size, len(rows))
