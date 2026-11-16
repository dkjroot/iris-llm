from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_client_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep the checkout's local devices and server out of tests."""
    path = tmp_path / "client_config.json"
    monkeypatch.setattr("iris_droid.client_settings.DEFAULT_CLIENT_CONFIG", path)
    return path
