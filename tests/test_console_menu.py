"""Native-menu navigation and async outcomes without SDL or a physical panel."""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from iris_droid.console_menu import ConsoleMenu
from iris_droid.face_console import DisplayState


@pytest.fixture(params=[(1024, 600), (480, 800)])
def menu(request):
    # Only drawing/font metrics are substituted; the actual layout and hit-testing run.
    pygame = Mock()

    def font(name, size, bold):
        def measure(text):
            return len(text) * max(1, size // 2), size

        return SimpleNamespace(
            size=measure,
            get_linesize=lambda: size + 3,
            render=lambda text, *args: SimpleNamespace(get_size=lambda: measure(text)),
        )

    pygame.font.SysFont.side_effect = font
    screen = Mock()
    screen.get_size.return_value = request.param
    chosen = []

    def select(name):
        result = Future()
        chosen.append((name, result))
        return result

    view = ConsoleMenu(pygame, screen, select)
    labels = {}
    original = view.button

    def button(rect, text, *args):
        labels[text] = rect
        original(rect, text, *args)

    view.button = button

    def press(label, now):
        left, top, width, height = labels[label]
        assert 0 <= left < view.width and left + width <= view.width
        assert 0 <= top < view.height and top + height <= view.height
        view.tap(left + width // 2, top + height // 2, now)

    return view, chosen, press


def state():
    return DisplayState(
        "signal",
        "idle",
        "neutral",
        "black",
        personalities=("friend", "scientist"),
        personality="friend",
        wake_phrase="hello iris",
    )


def test_touch_menu_navigates_shared_commands_and_expires(menu):
    view, chosen, press = menu
    assert not view.draw(state(), 0)
    view.tap(100, 100, 1)
    assert view.draw(state(), 1)
    press("Command list", 2)
    assert view.draw(state(), 2)
    press("Conversation", 3)
    assert view.draw(state(), 3)
    assert view.page == "category" and view.category["title"] == "Conversation"
    press("←", 4)
    assert view.draw(state(), 4) and view.page == "commands"
    press("×", 5)
    assert not view.draw(state(), 5)
    view.tap(100, 100, 10)
    assert view.draw(state(), 189)
    assert not view.draw(state(), 190)
    assert not chosen


def test_personality_touch_waits_for_result_and_allows_retry(menu):
    view, chosen, press = menu
    view.tap(100, 100, 1)
    view.draw(state(), 1)
    press("Personalities", 2)
    view.draw(state(), 2)
    press("scientist", 3)
    view.draw(state(), 3)
    press("friend ✓", 4)
    assert len(chosen) == 1 and chosen[0][0] == "scientist"
    chosen[0][1].set_result(False)
    view.draw(state(), 5)
    assert "Couldn’t switch" in view.error
    press("scientist", 6)
    assert len(chosen) == 2
    chosen[1][1].set_result(True)
    view.draw(state(), 7)
    assert view.selection is None and view.error == ""


def test_menu_waits_for_client_and_clamps_pages_when_catalog_shrinks(menu):
    view, chosen, press = menu
    view.tap(100, 100, 1)
    view.go("personalities")
    assert view.draw(None, 1)
    many = DisplayState(
        "signal", "idle", "neutral", "black", personalities=tuple(map(str, range(30)))
    )
    assert view.draw(many, 2)
    press("Next ›", 3)
    assert view.offset > 0
    assert view.draw(state(), 4)
    assert view.offset == 0
    assert not chosen


def test_wrapping_preserves_long_names(menu):
    view, _, _ = menu
    text = "averylongunbrokenpersonalityname"
    lines = view.wrap(text, 25, 20)
    assert len(lines) > 1 and "".join(lines) == text
    assert all(view.font(20, False).size(line)[0] <= 25 for line in lines)
