"""Construct the PC's selected recogniser without importing unused model libraries."""

from __future__ import annotations

import importlib
from typing import cast

from iris_settings import Settings
from iris_stt_engines.ISTT import ISTT


def make_stt(settings: Settings) -> ISTT:
    module = {
        "whisper": "whisper_offline",
        "wav2vec2": "wav2vec2_offline",
        "google": "google_online",
    }[settings.stt_engine]
    provider = importlib.import_module(f"iris_stt_engines.{module}")
    return cast(ISTT, provider.STT(settings))
