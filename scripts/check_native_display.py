"""Exercise the real SDL renderer without opening a window or an audio device.

Run with a Python environment containing Iris and Pygame. This deliberately uses
SDL's dummy display; KMS permissions, physical touch and panel quality are separate.
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import Future
from importlib.resources import files


def main() -> None:
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
    import pygame

    from iris_droid.console_menu import ConsoleMenu
    from iris_droid.expression import FACE_EXPRESSIONS
    from iris_droid.face_console import DisplayState, load_sprites, tap_position
    from iris_droid.face_display import FACE_HEADS

    pygame.display.init()
    pygame.font.init()
    frames = menus = 0
    manifest = json.loads(
        files("iris_droid").joinpath("face_sprites/sprites.json").read_text("utf-8")
    )
    selected: list[str] = []

    def select(name: str) -> Future[bool]:
        selected.append(name)
        result: Future[bool] = Future()
        result.set_result(True)
        return result

    try:
        for size in ((1024, 600), (600, 1024)):
            selected.clear()
            screen = pygame.display.set_mode(size)
            for head in FACE_HEADS:
                sprite = load_sprites(pygame, head, size)
                hashes = set()
                for expression in FACE_EXPRESSIONS:
                    animation_frames = manifest["frames"] + [
                        f"{blink}|{jaw}"
                        for blink in ("half-blink", "blink")
                        for jaw in ("jaw-small", "jaw-medium", "jaw-wide")
                    ]
                    for activity in animation_frames:
                        screen.fill((0, 0, 0))
                        pixels = sprite(expression, activity)
                        width, height = pixels.get_size()
                        screen.blit(pixels, ((size[0] - width) // 2, (size[1] - height) // 2))
                        rgb = pygame.image.tobytes(screen, "RGB")
                        assert any(rgb), (head, expression, activity, "blank frame")
                        hashes.add(hashlib.sha256(rgb).digest())
                        frames += 1
                assert len(hashes) > 4, (head, "expressions/animation did not change")

            state = DisplayState(
                "rivet",
                "listening",
                "curious",
                "black",
                personalities=("Test voice",),
                personality="Test voice",
                wake_phrase="Hey Iris",
            )
            menu = ConsoleMenu(pygame, screen, select)
            menu.deadline = 100
            for page in ("main", "commands", "home", "personalities", "category"):
                categories = menu.commands["categories"] if page == "category" else [menu.category]
                for category in categories:
                    menu.category = category
                    menu.go(page)
                    assert menu.draw(state, 1)
                    assert menu.buttons, (size, page, "no touch targets")
                    assert len(set(pygame.image.tobytes(screen, "RGB"))) > 10
                    menus += 1
            menu.go("personalities")
            assert menu.draw(state, 1)
            # Header navigation is first; the sole personality is the final tile.
            (x, y, width, height), _ = menu.buttons[-1]
            event = pygame.event.Event(
                pygame.FINGERUP, x=(x + width / 2) / size[0], y=(y + height / 2) / size[1]
            )
            point = tap_position(pygame, event, size)
            assert point is not None
            menu.tap(*point, 2)
            assert selected == ["Test voice"]
            assert menu.draw(state, 3)
            pygame.display.flip()
        print(f"PASS: {frames} native sprite frames, {menus} menu pages, both touch orientations")
        print(f"Pygame {pygame.version.ver}; SDL {pygame.get_sdl_version()}; dummy video driver")
    finally:
        pygame.quit()


if __name__ == "__main__":
    main()
