"""Update recovery keeps ownership, installation and readiness separate."""

import io
import json
import subprocess
from unittest.mock import Mock

import pytest

from scripts import iris_autoupdate as updater
from tests import test_autoupdate
from tests.test_autoupdate import commit, git

repos = test_autoupdate.repos


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(updater.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(updater.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


def test_quiesce_before_merge_and_resume_readiness_without_reinstall(repos):
    upstream, repo = repos
    before = git(repo.path, "rev-parse", "HEAD")
    (upstream / "code.txt").write_text("updated", encoding="utf-8")
    target = commit(upstream, "update")
    events = []

    def prepare(pending):
        events.append(("stop", git(repo.path, "rev-parse", "HEAD")))
        pending.services = ["iris-client.service"]
        repo.save_pending(pending)

    def install():
        assert git(repo.path, "rev-parse", "HEAD") == target
        events.append("install")

    def fail():
        assert repo.load_pending().stage == "verifying"
        raise updater.UpdateError("not ready")

    with pytest.raises(updater.UpdateError, match="not ready"):
        repo.update(install, lambda: events.append("start"), prepare=prepare, verify=fail)
    pending = repo.load_pending()
    assert pending.installed and pending.services == ["iris-client.service"]
    checkout = updater.Checkout(repo.path)
    checkout.update(install, lambda: events.append("start"), prepare=prepare, verify=lambda: None)
    assert events == [("stop", before), "install", "start", ("stop", target), "start"]
    assert not repo.pending_path.exists()


def test_interruption_before_merge_retains_service_selection(repos, monkeypatch):
    upstream, repo = repos
    (upstream / "code.txt").write_text("updated", encoding="utf-8")
    commit(upstream, "update")
    state = {"iris-client.service": "active", "unused.service": "inactive"}
    stopped = []

    def run(argv, **kwargs):
        name = argv[-1]
        assert repo.load_pending().services == ["iris-client.service"]
        state[name] = "inactive"
        stopped.append(name)
        raise OSError("interrupted after stop")

    managed = updater.ManagedServices(list(state), repo.save_pending)
    monkeypatch.setattr(managed, "state", lambda name: {"ActiveState": state[name]})
    # Only replace systemctl; Git must still run against the disposable repositories.
    real_run = subprocess.run
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda argv, **kw: run(argv, **kw) if argv[0] == "systemctl" else real_run(argv, **kw),
    )
    before = git(repo.path, "rev-parse", "HEAD")
    with pytest.raises(OSError, match="interrupted"):
        repo.update(lambda: None, lambda: None, prepare=managed.prepare)
    assert git(repo.path, "rev-parse", "HEAD") == before
    pending = repo.load_pending()
    monkeypatch.setattr(subprocess, "run", real_run)
    calls = []
    monkeypatch.setattr(updater.subprocess, "run", lambda argv, **kw: calls.append(argv))
    managed.prepare(pending)
    assert managed.selected == ["iris-client.service"]
    assert calls[0][2] == "stop"
    assert stopped == ["iris-client.service"]


def test_head_moved_or_dirty_after_restart_keeps_pending(repos):
    upstream, repo = repos
    (upstream / "code.txt").write_text("updated", encoding="utf-8")
    commit(upstream, "update")

    def edit():
        (repo.path / "code.txt").write_text("local edit", encoding="utf-8")

    with pytest.raises(updater.UpdateError, match="local edits"):
        repo.update(lambda: None, edit, verify=lambda: None)
    assert repo.pending_path.exists()
    commit(repo.path, "manual update")
    with pytest.raises(updater.UpdateError, match="HEAD moved"):
        repo.update(lambda: pytest.fail("install"), lambda: pytest.fail("restart"))


@pytest.mark.parametrize(
    "mutation",
    [{"installed": "yes"}, {"stage": []}, {"services": ["--help"]}, {"target": "not-a-commit"}],
)
def test_invalid_pending_never_runs_hooks(repos, mutation):
    _, repo = repos
    pending = {"previous": "a" * 40, "target": "b" * 40, **mutation}
    # A leading dash is not a unit we accept from a saved update.
    repo.pending_path.write_text(json.dumps(pending), encoding="utf-8")
    with pytest.raises(updater.UpdateError):
        repo.update(lambda: pytest.fail("install"), lambda: pytest.fail("restart"))


def test_legacy_pending_state_loads(repos):
    _, repo = repos
    repo.pending_path.write_text(
        json.dumps({"previous": "a" * 40, "target": "b" * 40, "installed": True}), encoding="utf-8"
    )
    assert repo.load_pending() == updater.PendingUpdate("a" * 40, "b" * 40, True)


def test_failed_service_is_recovered_inactive_and_missing_remain_stopped(monkeypatch):
    names = ["active.service", "failed.service", "inactive.service", "missing.service"]
    states = dict(zip(names, ["active", "failed", "inactive", "not-found"], strict=True))
    saved = []
    managed = updater.ManagedServices(names, lambda p: saved.append(list(p.services)))
    monkeypatch.setattr(managed, "state", lambda name: {"ActiveState": states[name]})
    calls = []

    def run(argv, **kwargs):
        assert saved == [["active.service", "failed.service"]]
        calls.append((argv[2], argv[-1]))
        states[argv[-1]] = "inactive"
        if argv[2] == "start" and argv[-1] == "active.service":
            raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(subprocess, "run", run)
    pending = updater.PendingUpdate("a" * 40, "b" * 40)
    managed.prepare(pending)
    with pytest.raises(updater.UpdateError, match="active.service"):
        managed.start()
    assert calls == [(action, name) for action in ["stop", "start"] for name in names[:2]]


def test_unstopped_service_blocks_install(monkeypatch):
    managed = updater.ManagedServices(["client.service"], lambda p: None)
    monkeypatch.setattr(managed, "state", lambda name: {"ActiveState": "active"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: None)
    with pytest.raises(updater.UpdateError, match="did not stop"):
        managed.prepare(updater.PendingUpdate("a" * 40, "b" * 40))


def test_restart_loop_does_not_pass_readiness(monkeypatch, clock):
    managed = updater.ManagedServices(["client.service"], lambda p: None)
    managed.selected = managed.names
    monkeypatch.setattr(
        managed,
        "state",
        lambda name: {
            "ActiveState": "active",
            "MainPID": str(100 + int(clock[0] * 4)),
            "NRestarts": "0",
        },
    )
    with pytest.raises(updater.UpdateError, match="readiness failed"):
        managed.verify(stable=1, timeout=2)
    monkeypatch.setattr(
        managed, "state", lambda name: {"ActiveState": "active", "MainPID": "100", "NRestarts": "0"}
    )
    managed.verify(stable=1, timeout=2)
    assert clock[0] == 3


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.setenv("IRIS_DROID_TOKEN", "synthetic-secret")
    return updater.HealthProbe("http://127.0.0.1:8765/v1/health", timeout=2, stable=0.5)


def response(value):
    return io.BytesIO(json.dumps(value).encode())


def test_probe_requires_new_instance_and_continuous_readiness(probe, clock):
    payloads = iter(
        [
            {"status": "ready", "instance": "old"},
            {"status": "ready", "instance": "new"},
            {"status": "busy", "instance": "new"},
            *[{"status": "ready", "instance": "new"}] * 3,
        ]
    )
    requests = []

    def open_request(request, **kwargs):
        requests.append(request)
        return response(next(payloads))

    probe.opener.open = open_request
    probe.wait(instance=lambda: "new")
    assert clock[0] == 1.25
    assert requests[0].get_header("Authorization") == "Bearer synthetic-secret"
    assert requests[0].get_method() == "GET"


@pytest.mark.parametrize("body", [b"not-json", b"x" * 65537, b"[]", b'{"status":"busy"}'])
def test_bad_health_response_times_out_without_logging_body(probe, clock, capsys, body):
    probe.opener.open = lambda *a, **kw: io.BytesIO(body)
    with pytest.raises(updater.UpdateError, match="timed out"):
        probe.wait()
    assert clock[0] == 2
    assert capsys.readouterr().out == ""


def test_process_exit_after_start_is_a_failure(probe, clock):
    probe.opener.open = lambda *a, **kw: response({"status": "ready"})
    with pytest.raises(updater.UpdateError, match="process exited"):
        probe.wait(running=lambda: clock[0] < 0.25)


def test_home_probe_posts_and_checks_scheduler(probe, clock):
    probe.kind = "home"
    requests = []

    def open_request(request, **kwargs):
        requests.append(request)
        return response({"scheduler": {"state": "ready"}})

    probe.opener.open = open_request
    probe.wait()
    assert requests[0].get_method() == "POST" and requests[0].data == b"{}"


def test_probe_does_not_follow_redirects():
    assert updater.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other") is None


def test_missing_token_and_url_credentials_fail_before_start(monkeypatch):
    monkeypatch.delenv("IRIS_DROID_TOKEN", raising=False)
    with pytest.raises(updater.UpdateError, match="environment"):
        updater.HealthProbe("http://localhost/v1/health")
    with pytest.raises(updater.UpdateError, match="without credentials"):
        updater.HealthProbe("http://user:secret@localhost/v1/health")


def test_windows_launch_passes_fresh_identity_and_stops_tree(monkeypatch, tmp_path):
    process = Mock(pid=123)
    process.poll.return_value = None
    spawn = Mock(return_value=process)
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    monkeypatch.setattr(subprocess, "run", run)
    server = updater.WindowsServer(tmp_path, tmp_path / "run.ps1", "powershell.exe")
    monkeypatch.setattr(server, "wait", lambda timeout: None)
    server.start()
    first = server.instance
    assert spawn.call_args.kwargs["env"]["IRIS_INSTANCE_ID"] == first
    server.restart()
    assert server.instance != first
    assert run.call_args.args[0] == ["taskkill", "/PID", "123", "/T", "/F"]
    process.wait.assert_called_once_with(timeout=10)


def test_service_main_wires_stop_install_start_and_health_in_order(repos, monkeypatch):
    import sys

    upstream, repo = repos
    (upstream / "code.txt").write_text("updated", encoding="utf-8")
    commit(upstream, "update")
    states = {"iris-client.service": "active", "iris-home.service": "failed"}
    events = []
    real_run = subprocess.run

    def run(argv, **kwargs):
        if argv[0] != "systemctl":
            return real_run(argv, **kwargs)
        name = argv[-1]
        action = argv[2]
        if action == "show":
            return subprocess.CompletedProcess(
                argv,
                0,
                stdout=(
                    f"LoadState=loaded\nActiveState={states[name]}\nMainPID=100\nNRestarts=0\n"
                ),
            )
        events.append((action, name))
        if action == "stop":
            assert (repo.path / "code.txt").read_text() == "original"
            states[name] = "inactive"
        elif action == "start":
            assert (repo.path / "local.txt").read_text() == "installed"
            states[name] = "active"
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", run)
    verify = updater.ManagedServices.verify
    monkeypatch.setattr(updater.ManagedServices, "verify", lambda self: verify(self, stable=0))
    health = Mock()
    health.wait.side_effect = lambda: events.append("health")
    monkeypatch.setattr(updater, "HealthProbe", Mock(return_value=health))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "updater",
            "--repo",
            str(repo.path),
            "--service",
            "iris-client.service",
            "--health-url",
            "http://localhost/v1/health",
            "--install-command",
            sys.executable,
            "-c",
            "from pathlib import Path; Path('local.txt').write_text('installed')",
        ],
    )
    assert updater.main() == 0
    assert events == [(action, name) for action in ["stop", "start"] for name in states] + [
        "health"
    ]
    assert not repo.pending_path.exists()


def test_windows_main_wires_process_identity_and_readiness(repos, monkeypatch):
    import os
    import sys
    from contextlib import nullcontext
    from types import SimpleNamespace

    upstream, repo = repos
    (upstream / "code.txt").write_text("updated", encoding="utf-8")
    commit(upstream, "update")
    events = []
    server = Mock(process=None, instance="a" * 32)
    server.wait.return_value = 0
    server.stop.side_effect = lambda: events.append("stop")

    def restart():
        assert (repo.path / "local.txt").read_text() == "installed"
        events.append("start")
        server.process = Mock()
        server.process.poll.return_value = None

    server.restart.side_effect = restart
    probe = Mock()

    def wait(running, instance):
        assert running() and instance() == server.instance
        assert repo.load_pending().stage == "verifying"
        events.append("health")

    probe.wait.side_effect = wait
    # Replace this module's OS view, not os.name globally (Path must remain native).
    monkeypatch.setattr(updater, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setattr(updater, "updater_lock", lambda path: nullcontext())
    monkeypatch.setattr(updater, "WindowsServer", Mock(return_value=server))
    monkeypatch.setattr(updater, "HealthProbe", Mock(return_value=probe))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "updater",
            "--repo",
            str(repo.path),
            "--launcher",
            "code.txt",
            "--install-command",
            sys.executable,
            "-c",
            "from pathlib import Path; Path('local.txt').write_text('installed')",
        ],
    )
    assert updater.main() == 0
    assert events == ["stop", "start", "health", "stop"]
    assert not repo.pending_path.exists()


def test_real_http_probe_authenticates_and_refuses_redirects(monkeypatch, clock):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/unexpected")
                self.end_headers()
                return
            body = json.dumps({"status": "ready", "instance": "current"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    monkeypatch.setenv("IRIS_DROID_TOKEN", "synthetic-token")
    with HTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}"
            updater.HealthProbe(url + "/health", stable=0).wait(instance=lambda: "current")
            assert seen == [("/health", "Bearer synthetic-token")]
            with pytest.raises(updater.UpdateError, match="timed out"):
                updater.HealthProbe(url + "/redirect", timeout=0.5).wait()
            assert all(path != "/unexpected" for path, _ in seen)
        finally:
            server.shutdown()
            thread.join(2)


def test_documented_home_probe_reaches_real_service(tmp_path, monkeypatch):
    import re
    from pathlib import Path
    from threading import Thread
    from zoneinfo import ZoneInfo

    from iris_home.devices import Device
    from iris_home.schedule import Scheduler
    from iris_home.service import HomeAPI, HomeServer
    from tests.support import FakeSwitch

    # Extract the documented route so a plausible but nonexistent URL fails this test.
    guide = (Path(__file__).resolve().parents[1] / "docs/updates.md").read_text(encoding="utf-8")
    paths = re.findall(r"--health-url https?://[^/\s]+(/v1/[a-z-]+)", guide)
    assert paths
    token = "a" * 64
    monkeypatch.setenv("IRIS_HOME_TOKEN", token)
    devices = {"test lamp": Device(FakeSwitch(), {})}
    with Scheduler(tmp_path / "home.sqlite3", ZoneInfo("UTC"), devices) as scheduler:
        with HomeServer(("127.0.0.1", 0), HomeAPI(devices, scheduler), token, None) as service:
            thread = Thread(target=service.serve_forever)
            thread.start()
            try:
                for path in paths:
                    probe = updater.HealthProbe(
                        f"http://127.0.0.1:{service.server_port}{path}",
                        kind="home",
                        token_env="IRIS_HOME_TOKEN",
                        timeout=3,
                        stable=0,
                    )
                    probe.wait()
                assert devices["test lamp"].switch.state() == "off"
            finally:
                service.shutdown()
                thread.join(2)
                assert not thread.is_alive()
