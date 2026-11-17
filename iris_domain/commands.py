"""Exact built-in command grammar shared by all transports."""


def companion_command(text: str) -> bool | None:
    phrase = " ".join(text.casefold().split()).strip(" .!?,")
    return {"companion mode on": True, "companion mode off": False}.get(phrase)


def is_stop_listening(text: str) -> bool:
    return " ".join(text.casefold().split()).strip(" .!?,") == "stop listening"


def is_clear_context(text: str) -> bool:
    return " ".join(text.casefold().split()).strip(" .!?,") == "clear context"


def is_delete_previous(text: str) -> bool:
    return " ".join(text.casefold().split()).strip(" .!?,") == "delete previous"
