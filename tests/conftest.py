from contextlib import ExitStack
from pathlib import Path

import pytest

from iris_chat_engines.messages import Message
from iris_settings import Settings
from tests.support import FakeClock, FakeSwitch, ScriptedBackend, make_harness


def pytest_addoption(parser):
    parser.addoption("--browser", action="store_true", help="Run the browser/JavaScript checks")
    parser.addoption("--artifacts", action="store_true", help="Build and check the installed wheel")


def pytest_collection_modifyitems(config, items):
    for marker, option in (("browser", "--browser"), ("artifact", "--artifacts")):
        if not config.getoption(option):
            for item in items:
                if marker in item.keywords:
                    item.add_marker(pytest.mark.skip(reason=f"Enable with {option}"))


@pytest.fixture
def fake_clock():
    return FakeClock()


@pytest.fixture
def fake_switch():
    return FakeSwitch()


@pytest.fixture
def brain_factory(tmp_path):
    with ExitStack() as resources:
        counter = 0

        def create(*, replies=None, **options):
            nonlocal counter
            counter += 1
            settings = Settings(
                **{
                    "base_url": "http://model.invalid/v1",
                    "allow_remote": True,
                    "memory_database": tmp_path / f"archive-{counter}.sqlite3",
                    "memory_hierarchy_enabled": False,
                    **options,
                }
            )
            model = ScriptedBackend(
                [Message(role="assistant", content="Hello.")] if replies is None else replies
            )
            harness = make_harness(settings, model)
            resources.callback(harness.transport.close)
            return harness

        yield create


@pytest.fixture(autouse=True)
def isolated_client_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep the checkout's local devices and server out of tests."""
    path = tmp_path / "client_config.json"
    monkeypatch.setattr("iris_droid.client_settings.DEFAULT_CLIENT_CONFIG", path)
    monkeypatch.setattr("iris_paths.PROJECT_ROOT", tmp_path)
    return path
