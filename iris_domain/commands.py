"""Whole-utterance built-in command grammar shared by all transports."""

import re


def control_phrase(text: str) -> str:
    """Allow polite wrappers and ASR punctuation, never substring commands."""
    phrase = " ".join(text.casefold().replace("’", "'").split()).strip(" .!?,")
    phrase = re.sub(r",\s*", " ", phrase)
    phrase = re.sub(r"^(?:please |(?:can|could|would) you (?:please )?)", "", phrase)
    return re.sub(r" please$", "", phrase).strip()


def companion_command(text: str) -> bool | None:
    phrase = control_phrase(text).replace("the companion mode", "companion mode")
    for enabled, words in ((True, ("on", "enable", "start")), (False, ("off", "disable", "stop"))):
        state, verb, alternate = words
        if phrase in {
            f"companion mode {state}",
            f"turn companion mode {state}",
            f"switch companion mode {state}",
            f"turn {state} companion mode",
            f"switch {state} companion mode",
            f"{verb} companion mode",
            f"{alternate} companion mode",
        }:
            return enabled
    return None


def is_companion_status(text: str) -> bool:
    return control_phrase(text) in {
        "companion mode status",
        "is companion mode on",
        "is companion mode off",
        "is companion mode enabled",
        "what is companion mode status",
        "what's companion mode status",
    }


def is_stop_listening(text: str) -> bool:
    return " ".join(text.casefold().split()).strip(" .!?,") == "stop listening"


def is_clear_context(text: str) -> bool:
    return " ".join(text.casefold().split()).strip(" .!?,") == "clear context"


def is_delete_previous(text: str) -> bool:
    return " ".join(text.casefold().split()).strip(" .!?,") == "delete previous"
