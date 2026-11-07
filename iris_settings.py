"""Read configuration before we go anywhere near a model or microphone."""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, JsonValue, SecretStr, field_validator, model_validator
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
    conversation_style_prompt: str = (
        "Iris is curious without interviewing her friend. She's comfortable letting a thought "
        "stand; she asks a conversational follow-up roughly one reply in four, while still "
        "asking for clarification when needed."
    )
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int = Field(default=250, gt=0, le=32768)
    model_context_tokens: int = Field(default=8192, ge=2048, le=2000000)
    context_margin_tokens: int = Field(default=512, ge=128, le=32768)
    memory_summary_tokens: int = Field(default=1200, ge=256, le=8192)
    memory_summary_interval: int = Field(default=8, ge=1, le=100)
    memory_summary_chars: int = Field(default=3000, ge=0, le=16000)
    memory_retrieval_chars: int = Field(default=3000, ge=0, le=16000)
    history_turns: int = Field(default=12, ge=0, le=100)
    memory_enabled: bool = False
    memory_database: Path | None = None
    memory_companion_followups: bool = False
    memory_search_tool: bool = True
    memory_hierarchy_enabled: bool = True
    memory_semantic_enabled: bool = False
    memory_embedding_model_dir: Path | None = None
    memory_semantic_min_score: float = Field(default=0.45, ge=0, le=1, allow_inf_nan=False)
    memory_owner: str = Field(default="default", min_length=1, max_length=100, pattern=r"^\S+$")
    memory_context_chars: int = Field(default=24000, ge=0, le=200000)
    timeout: float = Field(default=120, gt=0, le=600)
    trace: bool = False
    reply_encoding: Literal["text", "html", "double-html"] = "text"
    tools_enabled: bool = True
    chat_template_kwargs: dict[str, JsonValue] = Field(default_factory=dict)
    chat_request_options: dict[str, JsonValue] = Field(default_factory=dict)
    local_timezone: str = "Europe/London"
    weather_enabled: bool = False
    weather_location: str = "Stroud"
    weather_country: str = "GB"
    home_service_url: str | None = None
    home_service_ca: Path | None = None
    home_service_token: SecretStr | None = Field(default=None, exclude=True)
    notifications_enabled: bool = False
    pushover_api_token: SecretStr | None = Field(default=None, exclude=True)
    pushover_user_key: SecretStr | None = Field(default=None, exclude=True)
    pushover_device: str = Field(default="", max_length=25, pattern=r"^[A-Za-z0-9_-]*$")

    @field_validator("chat_request_options")
    @classmethod
    def valid_chat_request_options(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        reserved = {
            "model",
            "messages",
            "tools",
            "tool_choice",
            "stream",
            "temperature",
            "max_tokens",
            "chat_template_kwargs",
        }
        if reserved.intersection(value):
            raise ValueError(
                "chat_request_options cannot replace conversation/tool fields or named settings"
            )
        return value

    @field_validator("local_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            # Windows usually relies on tzdata rather than a system IANA database.
            try:
                ZoneInfo("UTC")
            except ZoneInfoNotFoundError:
                raise ValueError(
                    "Timezone data are missing. In your Iris environment, run "
                    "python -m pip install tzdata, then restart Iris."
                ) from error
            raise ValueError("Use an IANA timezone such as Europe/London") from error
        except ValueError as error:
            raise ValueError("Use an IANA timezone such as Europe/London") from error
        return value

    @field_validator("weather_location")
    @classmethod
    def valid_weather_location(cls, value: str) -> str:
        if not 1 <= len(value.strip()) <= 100:
            raise ValueError("Weather location must be a town name of 1–100 characters")
        return value.strip()

    @field_validator("weather_country")
    @classmethod
    def valid_weather_country(cls, value: str) -> str:
        if len(value) != 2 or not value.isascii() or not value.isalpha():
            raise ValueError("Use a two-letter country code such as GB")
        return value.upper()

    stt_engine: Literal["whisper", "wav2vec2", "google"] = "whisper"
    whisper_model: str = "base.en"
    device: Literal["cpu", "cuda", "auto"] = "cpu"
    compute_type: str = "int8"
    language: str | None = "en"
    tokenizer_model: str = "jonatasgrosman/wav2vec2-large-xlsr-53-english"
    ctc_model: str = "jonatasgrosman/wav2vec2-large-xlsr-53-english"
    tts_engine: Literal["kokoro"] = "kokoro"
    kokoro_voice: str = Field(default="af_heart", pattern=r"^[ab][fm]_[a-z]+$")
    kokoro_speed: float = Field(default=1.0, ge=0.5, le=2.0)
    kokoro_model_dir: Path | None = None
    phrase_time_limit: float = Field(default=60, gt=0, le=300)

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

    @field_validator("home_service_url")
    @classmethod
    def validate_home_service_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = cls.validate_endpoint(value)
        endpoint = urlsplit(value)
        if endpoint.scheme != "https" and endpoint.hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("Use HTTPS for a home service on another machine")
        return value

    @field_validator("model", "whisper_model", "tokenizer_model", "ctc_model", "compute_type")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("This value must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def check_privacy(self) -> Settings:
        if self.memory_semantic_enabled and not self.memory_enabled:
            raise ValueError("memory_semantic_enabled requires memory_enabled=true")
        output_tokens = (
            max(self.max_tokens, self.memory_summary_tokens)
            if self.memory_enabled
            else self.max_tokens
        )
        if output_tokens + self.context_margin_tokens + 1024 >= self.model_context_tokens:
            raise ValueError(
                "model_context_tokens must leave room for prompts, replies and summaries"
            )
        if self.tools_enabled and self.home_service_url:
            token = self.home_service_token.get_secret_value() if self.home_service_token else ""
            if len(token) < 32 or not token.isascii() or not token.isalnum():
                raise ValueError(
                    "Set IRIS_HOME_SERVICE_TOKEN to at least 32 ASCII letters or digits"
                )
        host = urlsplit(self.base_url).hostname
        if not self.allow_remote and (
            host not in {"localhost", "127.0.0.1", "::1"} or self.stt_engine == "google"
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
    for key in (
        "activation",
        "sounds",
        "microphone_index",
        "listen_timeout",
        "voice",
        "rate",
        "lang",
        "tld",
    ):
        if key in result:
            result.pop(key)
    if result.get("tts_engine") in ("pyttsx3", "gtts"):
        result["tts_engine"] = "kokoro"
        warnings.warn(
            "Iris now uses Kokoro for droid speech. Run configure.py to choose a voice.",
            stacklevel=2,
        )
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
