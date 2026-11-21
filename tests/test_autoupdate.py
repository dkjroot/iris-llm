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


def test_rollback_on_verify_failure_skips_bad_commit_until_upstream_advances(
    repos: tuple[Path, Checkout],
) -> None:
    upstream, repo = repos
    before = git(repo.path, "rev-parse", "HEAD")
    (upstream / "code.txt").write_text("broken", encoding="utf-8")
    bad = commit(upstream, "Broken release")
    events: list[str] = []

    def install() -> None:
        events.append("install:" + (repo.path / "code.txt").read_text(encoding="utf-8"))

    def restart() -> None:
        events.append("restart:" + (repo.path / "code.txt").read_text(encoding="utf-8"))

    def verify() -> None:
        current = (repo.path / "code.txt").read_text(encoding="utf-8")
        events.append("verify:" + current)
        if current == "broken":
            raise UpdateError("health failed")

    with pytest.raises(UpdateError, match="rolled back"):
        repo.update(install, restart, verify=verify, rollback_on_failure=True)
    assert git(repo.path, "rev-parse", "HEAD") == before
    assert not repo.pending_path.exists()
    assert repo.load_failed() == bad
    assert events == [
        "install:broken",
        "restart:broken",
        "verify:broken",
        "install:original",
        "restart:original",
        "verify:original",
    ]

    # Next timer tick skips the failed commit cleanly without touching services.
    events.clear()
    repo.update(install, restart, verify=verify, rollback_on_failure=True)
    assert events == []
    assert git(repo.path, "rev-parse", "HEAD") == before

    # Once upstream advances past the broken commit, the updater applies the fix and clears state.
    (upstream / "code.txt").write_text("fixed", encoding="utf-8")
    fixed = commit(upstream, "Fixed release")
    repo.update(install, restart, verify=verify, rollback_on_failure=True)
    assert git(repo.path, "rev-parse", "HEAD") == fixed
    assert repo.load_failed() is None
    assert events == ["install:fixed", "restart:fixed", "verify:fixed"]


def test_merge_failure_restarts_stopped_services_and_clears_pending(
    repos: tuple[Path, Checkout],
) -> None:
    upstream, repo = repos
    (upstream / ".gitignore").write_text("", encoding="utf-8")
    (upstream / "local.txt").write_text("upstream", encoding="utf-8")
    (repo.path / "local.txt").write_text("settings", encoding="utf-8")
    commit(upstream, "Collision")
    events: list[str] = []

    with pytest.raises(UpdateError):
        repo.update(
            lambda: events.append("install"),
            lambda: events.append("restart"),
            prepare=lambda _: events.append("stop"),
            rollback_on_failure=True,
        )
    assert events == ["stop", "restart"]
    assert not repo.pending_path.exists()


def test_health_probe_rejects_control_characters_in_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.iris_autoupdate import HealthProbe

    monkeypatch.setenv("IRIS_DROID_TOKEN", "bad\r\nX-Injected: 1")
    with pytest.raises(UpdateError, match="environment"):
        HealthProbe("http://127.0.0.1:8765/v1/health")
