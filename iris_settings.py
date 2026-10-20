"""Read configuration before we go anywhere near a model or microphone."""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IRIS_", extra="forbid", hide_input_in_errors=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return env_settings, init_settings, file_secret_settings

    base_url: str = "http://127.0.0.1:5001/v1"
    model: str = "local-model"
    api_key: SecretStr | None = Field(default=None, exclude=True)
    allow_remote: bool = False
    system_prompt: str = "You are Iris, a friendly assistant. Keep spoken replies fairly short."
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=250, gt=0, le=32768)
    history_turns: int = Field(default=12, ge=0, le=100)
    timeout: float = Field(default=120, gt=0, le=600)
    trace: bool = False
    reply_encoding: Literal["text", "html", "double-html"] = "text"

    stt_engine: Literal["whisper", "wav2vec2", "google"] = "whisper"
    whisper_model: str = "base.en"
    device: Literal["cpu", "cuda", "auto"] = "cpu"
    compute_type: str = "int8"
    language: str | None = "en"
    tokenizer_model: str = "jonatasgrosman/wav2vec2-large-xlsr-53-english"
    ctc_model: str = "jonatasgrosman/wav2vec2-large-xlsr-53-english"
    tts_engine: Literal["pyttsx3", "kokoro", "gtts"] = "pyttsx3"
    kokoro_voice: str = Field(default="af_heart", pattern=r"^[ab][fm]_[a-z]+$")
    kokoro_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    kokoro_model_dir: Path | None = None
    voice: str | None = None
    rate: int = Field(default=200, ge=50, le=500)
    lang: str = "en"
    tld: str = "com.au"
    sounds: bool = True
    activation: Literal["enter", "space", "wake", "continuous"] = "enter"
    microphone_index: int | None = Field(default=None, ge=0)
    listen_timeout: float = Field(default=10, gt=0, le=300)
    phrase_time_limit: float = Field(default=30, gt=0, le=300)

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        parts = urlsplit(value)
        # Accessing port also validates its syntax and range.
        _ = parts.port
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Use an http(s) base URL without credentials, query, or fragment")
        return value.rstrip("/")

    @field_validator("model", "whisper_model", "tokenizer_model", "ctc_model", "compute_type")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("This value must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def check_privacy(self) -> Settings:
        host = urlsplit(self.base_url).hostname
        if not self.allow_remote and (
            host not in {"localhost", "127.0.0.1", "::1"}
            or self.stt_engine == "google"
            or self.tts_engine == "gtts"
        ):
            raise ValueError("Remote endpoints or Google speech need allow_remote=true")
        return self


# Keep old config files useful, but never import a module named by a config file.
ALIASES: dict[str, tuple[str, dict[str, str | bool]]] = {
    "speech to text engine": (
        "stt_engine",
        {
            "iris_stt_engines.wav2vec2_offline": "wav2vec2",
            "iris_stt_engines.google_online": "google",
        },
    ),
    "text to speech engine": (
        "tts_engine",
        {
            "iris_tts_engines.pyttsx3_offline": "pyttsx3",
            "iris_tts_engines.gTTS_online": "gtts",
        },
    ),
    "push-to-talk / wake-word": (
        "activation",
        {
            "iris_ptt.push_space_to_talk": "space",
            "iris_ptt.wake_word": "wake",
            "iris_ptt.do_not_wait": "continuous",
        },
    ),
    "feedback sounds": (
        "sounds",
        {
            "iris_sounds.beeps_and_bleeps": True,
            "iris_sounds.no_sounds": False,
        },
    ),
}


def migrate_config(data: dict[str, object]) -> dict[str, object]:
    result = data.copy()
    for old, (new, choices) in ALIASES.items():
        if old in result:
            value = result.pop(old)
            if not isinstance(value, str) or value not in choices or new in result:
                raise ValueError(f"Check the old {old!r} setting; run configure.py to update it")
            result[new] = choices[value]
    if "chat engine" in result:
        engine = result.pop("chat engine")
        if engine == "iris_chat_engines.oobabooga_api_offline":
            result.setdefault("base_url", "http://127.0.0.1:5000/v1")
            warnings.warn("Old Oobabooga config: enable its current /v1 chat API.", stacklevel=2)
        elif engine == "iris_chat_engines.openai_gpt_online":
            if "base_url" not in result:
                raise ValueError("Old GPT config: set base_url, model and allow_remote explicitly")
            if result.get("model") == "text-davinci-002":
                raise ValueError(
                    "Replace the old text-davinci-002 setting with a current chat model"
                )
        else:
            raise ValueError("Unknown chat engine; run configure.py to update the configuration")
    # These belonged to the old completion prompt, rather than our chat interface.
    for key in ("top_p", "frequency_penalty", "presence_penalty"):
        if key in result:
            result.pop(key)
            warnings.warn(f"Ignoring legacy generation setting {key!r}.", stacklevel=2)
    return result


def load_settings(path: Path | None = None) -> Settings:
    # Environment values override the file; secrets never need to go in that file.
    data: dict[str, object] = {}
    if path is not None:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
            raise ValueError("The configuration must be a JSON object")
        data = migrate_config(raw)
    return Settings.model_validate(data)
