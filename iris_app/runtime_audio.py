"""Packaged spoken confirmations shared by application commands."""

from __future__ import annotations

import base64
import logging
from importlib.resources import files

LOGGER = logging.getLogger(__name__)


def command_audio(name: str, *, text_only: bool) -> str:
    if text_only:
        return ""
    try:
        wav = files("iris_sounds").joinpath(name).read_bytes()
    except OSError:
        LOGGER.warning("Spoken confirmation unavailable (%s).", name)
        return ""
    return base64.b64encode(wav).decode("ascii")
