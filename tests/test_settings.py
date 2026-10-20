import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from iris_settings import Settings, load_settings, migrate_config


@pytest.mark.parametrize(
    "data",
    [
        {"max_tokens": 0},
        {"temperature": -1},
        {"history_turns": -1},
        {"listen_timeout": 0},
        {"model": " "},
        {"stt_engine": "invented"},
        {"unknown": 1},
        {"base_url": "file:///tmp/model"},
        {"base_url": "http://user:password@localhost/v1"},
        {"base_url": "http://localhost/v1?key=secret"},
        {"base_url": "https://example.com/v1"},
        {"tts_engine": "gtts"},
        {"stt_engine": "google"},
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
    assert settings.activation == "continuous"
    assert not settings.sounds


def test_unknown_modules_and_old_implicit_hosted_endpoint_are_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown chat engine"):
        migrate_config({"chat engine": "os"})
    with pytest.raises(ValueError, match="set base_url"):
        migrate_config({"chat engine": "iris_chat_engines.openai_gpt_online"})


def test_distributed_default_and_template_match_model_defaults() -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("local-config-default.json", "config_template.json"):
        assert load_settings(root / name) == Settings()
