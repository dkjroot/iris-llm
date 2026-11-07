import subprocess
from pathlib import Path

import pytest

from scripts.iris_autoupdate import Checkout, UpdateError, updater_lock


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(repo: Path, message: str) -> str:
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=Iris Test",
        "-c",
        "user.email=iris@example.test",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        message,
    )
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Checkout]:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init")
    (upstream / "code.txt").write_text("original", encoding="utf-8")
    (upstream / ".gitignore").write_text("local.txt\n", encoding="utf-8")
    commit(upstream, "Initial")
    client = tmp_path / "client"
    git(tmp_path, "clone", str(upstream), str(client))
    return upstream, Checkout(client)


def test_fast_forward_once_and_keep_local_files(repos: tuple[Path, Checkout]) -> None:
    upstream, repo = repos
    local = repo.path / "local.txt"
    local.write_text("settings", encoding="utf-8")
    calls: list[str] = []

    def update() -> None:
        repo.update(lambda: calls.append("install"), lambda: calls.append("restart"))

    update()
    assert calls == []
    (upstream / "code.txt").write_text("new version", encoding="utf-8")
    target = commit(upstream, "New version")
    update()
    update()
    assert calls == ["install", "restart"]
    assert git(repo.path, "rev-parse", "HEAD") == target
    assert local.read_text(encoding="utf-8") == "settings"


@pytest.mark.parametrize("condition", ["dirty", "diverged", "offline", "ignored collision"])
def test_skip_unsafe_updates(repos: tuple[Path, Checkout], condition: str) -> None:
    upstream, repo = repos
    (upstream / "code.txt").write_text("new version", encoding="utf-8")
    if condition == "ignored collision":
        (upstream / ".gitignore").write_text("", encoding="utf-8")
        (upstream / "local.txt").write_text("upstream", encoding="utf-8")
        (repo.path / "local.txt").write_text("settings", encoding="utf-8")
    commit(upstream, "New version")
    if condition in {"dirty", "diverged"}:
        (repo.path / "code.txt").write_text("local edits", encoding="utf-8")
    if condition == "diverged":
        commit(repo.path, "Local version")
    if condition == "offline":
        upstream.rename(upstream.with_name("unavailable"))
    before = git(repo.path, "rev-parse", "HEAD")

    def unexpected() -> None:
        pytest.fail("Updater invoked a hook despite the failed update")

    with pytest.raises(UpdateError):
        repo.update(unexpected, unexpected)
    assert git(repo.path, "rev-parse", "HEAD") == before
    if condition == "ignored collision":
        assert (repo.path / "local.txt").read_text(encoding="utf-8") == "settings"


def test_retry_install_then_restart(repos: tuple[Path, Checkout]) -> None:
    upstream, repo = repos
    (upstream / "code.txt").write_text("new version", encoding="utf-8")
    commit(upstream, "New version")
    calls: list[str] = []

    def install() -> None:
        calls.append("install")
        if calls == ["install"]:
            raise UpdateError("installation failed")

    def restart() -> None:
        calls.append("restart")
        if calls.count("restart") == 1:
            raise UpdateError("restart failed")

    with pytest.raises(UpdateError, match="installation failed"):
        repo.update(install, restart)
    with pytest.raises(UpdateError, match="restart failed"):
        Checkout(repo.path).update(install, restart)
    Checkout(repo.path).update(install, restart)
    assert calls == ["install", "install", "restart", "restart"]
    assert not repo.pending_path.exists()


def test_checkout_lock(repos: tuple[Path, Checkout]) -> None:
    _, repo = repos
    with updater_lock(repo.lock_path):
        with pytest.raises(UpdateError, match="locked"), updater_lock(repo.lock_path):
            pytest.fail("A second updater got the lock")
    with updater_lock(repo.lock_path):
        pass


def test_restarts_active_client_and_home_service_and_skips_missing_unit(monkeypatch):
    import subprocess

    from scripts.iris_autoupdate import restart_services

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        name = argv[-1]
        if "show" in argv:
            state = "not-found" if name == "missing.service" else "loaded"
            return subprocess.CompletedProcess(argv, int(state == "not-found"), stdout=state)
        if "is-failed" in argv:
            return subprocess.CompletedProcess(argv, int(name != "iris-home.service"))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", run)
    restart_services(["iris-client.service", "iris-home.service", "missing.service"])
    assert [(argv[2], argv[-1]) for argv in calls if argv[2] in {"try-restart", "restart"}] == [
        ("try-restart", "iris-client.service"),
        ("restart", "iris-home.service"),
    ]


def test_home_service_is_restarted_even_if_client_restart_fails(monkeypatch):
    from scripts import iris_autoupdate

    attempts = []

    def restart(name):
        attempts.append(name)
        if name == "iris-client.service":
            raise OSError("client restart failed")

    monkeypatch.setattr(iris_autoupdate, "restart_service", restart)
    with pytest.raises(UpdateError, match="iris-client.service"):
        iris_autoupdate.restart_services(["iris-client.service", "iris-home.service"])
    assert attempts == ["iris-client.service", "iris-home.service"]
