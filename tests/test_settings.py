import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from pydantic import ValidationError

from iris_settings import Settings, load_settings, migrate_config


def test_missing_timezone_database_explains_install(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_zone(key: str) -> ZoneInfo:
        raise ZoneInfoNotFoundError(key)

    monkeypatch.setattr("iris_settings.ZoneInfo", missing_zone)
    with pytest.raises(ValidationError, match="python -m pip install tzdata"):
        Settings(local_timezone="Europe/London")


def test_unknown_timezone_with_database_explains_name(monkeypatch: pytest.MonkeyPatch) -> None:
    utc = ZoneInfo("UTC")

    def known_utc_only(key: str) -> ZoneInfo:
        if key == "UTC":
            return utc
        raise ZoneInfoNotFoundError(key)

    monkeypatch.setattr("iris_settings.ZoneInfo", known_utc_only)
    with pytest.raises(ValidationError, match="Use an IANA timezone"):
        Settings(local_timezone="Mars/Olympus")


@pytest.mark.parametrize(
    "data",
    [
        {"max_tokens": 0},
        {"temperature": -1},
        {"history_turns": -1},
        {"model": " "},
        {"stt_engine": "invented"},
        {"unknown": 1},
        {"base_url": "file:///tmp/model"},
        {"base_url": "http://user:password@localhost/v1"},
        {"base_url": "http://localhost/v1?key=secret"},
        {"base_url": "https://example.com/v1"},
        {"tts_engine": "gtts"},
        {"stt_engine": "google"},
        {"local_timezone": "Mars/Olympus"},
        {"weather_location": " "},
        {"weather_country": "UK-long"},
    ],
)
def test_bad_settings_fail_before_loading_providers(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(data)


def test_explicit_remote_mode_and_secret_not_saved() -> None:
    settings = Settings(base_url="https://example.com/v1/", allow_remote=True, api_key="secret")
    assert settings.base_url == "https://example.com/v1"
    assert "api_key" not in settings.model_dump()
    assert "secret" not in str(settings)


def test_environment_overrides_file_and_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "local-config.json"
    path.write_text(json.dumps({"model": "file-model", "max_tokens": 200}), encoding="utf-8")
    monkeypatch.setenv("IRIS_MODEL", "environment-model")
    monkeypatch.setenv("IRIS_MAX_TOKENS", "123")
    monkeypatch.setenv("IRIS_API_KEY", "private")
    settings = load_settings(path)
    assert settings.model == "environment-model"
    assert settings.max_tokens == 123
    assert settings.api_key.get_secret_value() == "private"


def test_file_opt_in_combines_with_environment_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"allow_remote": true}', encoding="utf-8")
    monkeypatch.setenv("IRIS_BASE_URL", "https://example.com/v1")
    assert load_settings(path).allow_remote


def test_legacy_configuration_has_a_known_migration() -> None:
    with pytest.warns(UserWarning, match="current /v1"):
        data = migrate_config(
            {
                "chat engine": "iris_chat_engines.oobabooga_api_offline",
                "speech to text engine": "iris_stt_engines.wav2vec2_offline",
                "push-to-talk / wake-word": "iris_ptt.do_not_wait",
                "feedback sounds": "iris_sounds.no_sounds",
            }
        )
    settings = Settings.model_validate(data)
    assert settings.base_url == "http://127.0.0.1:5000/v1"
    assert settings.stt_engine == "wav2vec2"
    assert "activation" not in settings.model_dump()
    assert "sounds" not in settings.model_dump()


def test_unknown_modules_and_old_implicit_hosted_endpoint_are_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown chat engine"):
        migrate_config({"chat engine": "os"})
    with pytest.raises(ValueError, match="set base_url"):
        migrate_config({"chat engine": "iris_chat_engines.openai_gpt_online"})


def test_distributed_default_and_template_match_model_defaults() -> None:
    root = Path(__file__).resolve().parents[1]
    assert load_settings(root / "config_template.json") == Settings()


def test_skill_settings_can_be_overridden_from_environment(monkeypatch):
    monkeypatch.setenv("IRIS_LOCAL_TIMEZONE", "Asia/Tokyo")
    monkeypatch.setenv("IRIS_WEATHER_LOCATION", "Tokyo")
    monkeypatch.setenv("IRIS_WEATHER_COUNTRY", "jp")
    monkeypatch.setenv("IRIS_WEATHER_ENABLED", "true")
    settings = Settings()
    assert settings.local_timezone == "Asia/Tokyo"
    assert settings.weather_location == "Tokyo" and settings.weather_country == "JP"
    assert settings.weather_enabled and not settings.allow_remote


def test_notification_credentials_are_private_and_environment_overrides_file(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text('{"pushover_api_token":"file-token","pushover_user_key":"file-user"}')
    monkeypatch.setenv("IRIS_PUSHOVER_API_TOKEN", "environment-token")
    monkeypatch.setenv("IRIS_PUSHOVER_USER_KEY", "environment-user")
    monkeypatch.setenv("IRIS_NOTIFICATIONS_ENABLED", "true")
    monkeypatch.setenv("IRIS_PUSHOVER_DEVICE", "iphone")
    settings = load_settings(path)
    assert settings.pushover_api_token.get_secret_value() == "environment-token"
    assert settings.pushover_user_key.get_secret_value() == "environment-user"
    assert settings.notifications_enabled and not settings.allow_remote
    assert settings.pushover_device == "iphone"
    assert "pushover_api_token" not in settings.model_dump()
    assert "pushover_user_key" not in settings.model_dump()
    assert "environment-token" not in str(settings) + settings.model_dump_json()
    assert "environment-user" not in str(settings) + settings.model_dump_json()


@pytest.mark.parametrize("device", ["iphone,other", "x" * 26, "phone with spaces"])
def test_pushover_device_must_be_a_single_registered_name(device):
    with pytest.raises(ValidationError):
        Settings(pushover_device=device)
