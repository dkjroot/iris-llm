import json
import struct
from argparse import ArgumentTypeError
from importlib.resources import files

import pytest

from iris_droid.expression import FACE_EXPRESSIONS
from iris_droid.face_console import (
    DisplayState,
    fit_size,
    frame_for,
    read_state,
    request_face_cycle,
    window_size,
)
from iris_droid.face_display import FACE_HEADS, FaceDisplay


def test_console_uses_the_running_voice_clients_display_state() -> None:
    display = FaceDisplay("rivet", "black")
    with display.serve(0) as port:
        assert port is not None
        assert read_state(port) == DisplayState("rivet", "idle", "neutral", "black")
        with display.speaking("amused"):
            assert read_state(port) == DisplayState("rivet", "speaking", "amused", "black")
        assert read_state(port) == DisplayState("rivet", "idle", "neutral", "black")
    assert read_state(port) is None


def test_console_cycles_previews_and_restores_the_selected_face(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    now = 100
    monkeypatch.setattr("iris_droid.face_display.monotonic", lambda: now)
    display = FaceDisplay("signal", "black", remember=True)
    with display.serve(0) as port:
        assert port is not None
        with display.speaking("amused"):
            for head in ("rivet", "humanoid-android", "sprout"):
                assert request_face_cycle(port)
                assert read_state(port) == DisplayState(
                    head, "speaking", "amused", "black", preview=True
                )
        assert read_state(port) == DisplayState("sprout", "idle", "neutral", "black", preview=True)
        now += 5
        assert read_state(port) == DisplayState("sprout", "idle", "neutral", "black")

    restored = FaceDisplay("signal", remember=True)
    assert restored.head == "sprout"
    assert FaceDisplay(None, remember=True).head is None
    (tmp_path / "iris" / "face").write_text("unknown\n")
    assert FaceDisplay("signal", remember=True).head == "signal"


def test_console_sprite_frames_follow_activity_and_expression() -> None:
    listening = DisplayState("rivet", "listening", "curious", "snow")
    thinking = DisplayState("rivet", "thinking", "neutral", "snow")
    speaking = DisplayState("rivet", "speaking", "amused", "snow")
    assert frame_for(listening, 0.095) == ("curious", "blink")
    assert frame_for(listening, 1) == ("curious", "rest")
    assert frame_for(thinking, 1) == ("focused", "thinking")
    assert frame_for(speaking, 1) == ("amused", "rest")
    assert frame_for(speaking, 0.48) == ("amused", "jaw-wide")


def test_sprite_sheets_include_all_heads_and_fit_the_lcd() -> None:
    root = files("iris_droid").joinpath("face_sprites")
    manifest = json.loads(root.joinpath("sprites.json").read_text("utf-8"))
    width, height = manifest["size"]
    x, y, tile_w, tile_h = manifest["crop"]
    assert x + tile_w <= width and y + tile_h <= height
    assert tuple(manifest["expressions"]) == FACE_EXPRESSIONS
    assert set(manifest["tilts"]) == set(FACE_HEADS)
    expected_size = tile_w * len(manifest["frames"]), height + tile_h * len(FACE_EXPRESSIONS)
    for head in FACE_HEADS:
        assert set(manifest["tilts"][head]) == set(FACE_EXPRESSIONS)
        pixels = root.joinpath(f"{head}.png").read_bytes()
        assert pixels.startswith(b"\x89PNG\r\n\x1a\n")
        assert struct.unpack(">II", pixels[16:24]) == expected_size
    assert fit_size((width, height), (800, 480)) == (501, 480)
    assert fit_size((width, height), (1024, 600)) == (627, 600)
    assert fit_size((width, height), (480, 800)) == (480, 459)


def test_window_size_accepts_hdmi_panel_dimensions() -> None:
    assert window_size("1024x600") == (1024, 600)
    assert window_size("600X1024") == (600, 1024)


@pytest.mark.parametrize("value", ["1024", "1024x", "1024x0", "-1024x600", "1024x600x60"])
def test_window_size_rejects_invalid_dimensions(value: str) -> None:
    with pytest.raises(ArgumentTypeError):
        window_size(value)
