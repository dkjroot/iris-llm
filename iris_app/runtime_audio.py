"""Packaged spoken confirmations shared by application commands."""

from __future__ import annotations

import base64
import logging
from functools import lru_cache
from importlib.resources import files

LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=16)
def _encoded_sound(name: str) -> str:
    wav = files("iris_sounds").joinpath(name).read_bytes()
    return base64.b64encode(wav).decode("ascii")


def command_audio(name: str, *, text_only: bool) -> str:
    if text_only:
        return ""
    try:
        return _encoded_sound(name)
    except OSError:
        LOGGER.warning("Spoken confirmation unavailable (%s).", name)
        return ""
