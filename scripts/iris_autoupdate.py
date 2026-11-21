"""Opt-in Git updates for an Iris checkout."""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


class UpdateError(Exception):
    pass


def log(message: str) -> None:
    print(message, flush=True)


@dataclass
class PendingUpdate:
    previous: str
    target: str
    installed: bool = False
    stage: str = "planned"
    services: list[str] | None = None


@contextmanager
def updater_lock(path: Path) -> Iterator[None]:
    with path.open("a+b") as handle:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise UpdateError("Another updater has this checkout locked.") from exc
        yield


class Checkout:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.git_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"}
        self.path = Path(self.git("rev-parse", "--show-toplevel")).resolve()
        state_dir = self.path / self.git("rev-parse", "--git-path", "iris-autoupdate")
        state_dir.mkdir(exist_ok=True)
        self.lock_path = state_dir / "lock"
        self.pending_path = state_dir / "pending.json"
        self.failed_path = state_dir / "failed.json"

    def git(self, *args: str, ok: tuple[int, ...] = (0,)) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=self.git_env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode not in ok:
            if args[0] == "fetch":
                raise UpdateError(
                    "Git fetch failed. Check network access and Git authentication "
                    "under the account and environment running the updater."
                )
            raise UpdateError(result.stderr.strip() or f"git {args[0]} failed.")
        return result.stdout.strip()

    def fetch_upstream(self) -> str:
        branch = self.git("symbolic-ref", "--quiet", "--short", "HEAD")
        remote = self.git("config", "--get", f"branch.{branch}.remote")
        if not remote or remote == ".":
            raise UpdateError("Configure a remote upstream for this branch first.")
        self.git("fetch", "--prune", "--no-recurse-submodules", "--", remote)
        return self.git("rev-parse", "--verify", "@{upstream}^{commit}")

    def require_clean(self) -> None:
        for name in (
            "MERGE_HEAD",
            "CHERRY_PICK_HEAD",
            "REVERT_HEAD",
            "rebase-merge",
            "rebase-apply",
            "sequencer",
            "BISECT_START",
        ):
            if (self.path / self.git("rev-parse", "--git-path", name)).exists():
                raise UpdateError("Finish the Git operation in this checkout before updating.")
        if self.git("status", "--porcelain", "--untracked-files=no"):
            raise UpdateError("Tracked files have local edits. Skipping the update.")

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        temp = path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            fsync = getattr(os, "fsync", None)
            if fsync is not None:
                fsync(handle.fileno())
        temp.replace(path)

    def save_pending(self, pending: PendingUpdate) -> None:
        self._atomic_write(self.pending_path, json.dumps(asdict(pending)))

    def save_failed(self, commit: str) -> None:
        self._atomic_write(self.failed_path, json.dumps({"failed_target": commit}))

    def load_failed(self) -> str | None:
        if not self.failed_path.exists():
            return None
        try:
            data = json.loads(self.failed_path.read_text(encoding="utf-8"))
            target = data.get("failed_target") if isinstance(data, dict) else None
            if isinstance(target, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", target):
                return target
        except (OSError, ValueError):
            pass
        return None

    def clear_failed(self) -> None:
        try:
            self.failed_path.unlink(missing_ok=True)
        except OSError:
            pass

    def load_pending(self) -> PendingUpdate:
        try:
            pending = PendingUpdate(**json.loads(self.pending_path.read_text(encoding="utf-8")))
            if (
                not all(
                    isinstance(value, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value)
                    for value in (pending.previous, pending.target)
                )
                or type(pending.installed) is not bool
                or pending.stage
                not in {"planned", "stopping", "installing", "starting", "verifying"}
                or pending.services is not None
                and (
                    not isinstance(pending.services, list)
                    or len(pending.services) > 64
                    or not all(
                        isinstance(name, str)
                        and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]{0,255}", name)
                        for name in pending.services
                    )
                )
            ):
                raise ValueError
            return pending
        except (ValueError, TypeError):
            raise UpdateError(
                "Invalid pending update state; review it before continuing."
            ) from None

    def update(
        self,
        install: Callable[[], None],
        restart: Callable[[], None],
        *,
        prepare: Callable[[PendingUpdate], None] | None = None,
        verify: Callable[[], None] | None = None,
        rollback_on_failure: bool = False,
    ) -> None:
        current = self.git("rev-parse", "HEAD")
        if self.pending_path.exists():
            pending = self.load_pending()
            if current not in {pending.previous, pending.target} or (
                pending.installed and current != pending.target
            ):
                raise UpdateError(
                    f"HEAD moved during a pending update. Review {self.pending_path} "
                    "and complete the update manually."
                )
            self.require_clean()
        else:
            target = self.fetch_upstream()
            if current == target:
                return
            if rollback_on_failure and self.load_failed() == target:
                log(
                    f"Skipping previously failed update {target[:12]} "
                    "until upstream advances past it."
                )
                return
            ahead, behind = map(
                int,
                self.git("rev-list", "--left-right", "--count", f"{current}...{target}").split(),
            )
            if not behind:
                return
            if ahead:
                raise UpdateError(
                    "The local branch and its upstream have diverged. Skipping the update."
                )
            self.require_clean()
            if self.git("rev-parse", "HEAD") != current:
                raise UpdateError("HEAD changed during the fetch. Skipping the update.")
            pending = PendingUpdate(current, target)
            self.save_pending(pending)
        # Save the intended service set before stopping anything, so an interrupted
        # install can start those same services even though they are now inactive.
        pending.stage = "stopping"
        self.save_pending(pending)
        if prepare is not None:
            prepare(pending)
        if self.git("rev-parse", "HEAD") != current:
            raise UpdateError("HEAD changed while stopping services; review the pending update.")
        if current == pending.previous:
            try:
                self.git("merge", "--ff-only", "--no-overwrite-ignore", pending.target)
            except UpdateError:
                if prepare is not None:
                    # Services were stopped before HEAD moved; bring them back up.
                    try:
                        restart()
                    except (UpdateError, OSError, subprocess.SubprocessError) as restart_err:
                        log(f"Could not restart services after merge failure: {restart_err}")
                    self.pending_path.unlink(missing_ok=True)
                raise
            log(f"Updated Iris from {current[:12]} to {pending.target[:12]}.")
        self.require_clean()
        self.finish(
            pending,
            install,
            restart,
            prepare=prepare,
            verify=verify,
            rollback_on_failure=rollback_on_failure,
        )

    def _rollback(
        self,
        pending: PendingUpdate,
        install: Callable[[], None],
        restart: Callable[[], None],
        *,
        prepare: Callable[[PendingUpdate], None] | None = None,
        verify: Callable[[], None] | None = None,
        reinstall: bool = False,
    ) -> None:
        log(f"Rolling back Iris from {pending.target[:12]} to {pending.previous[:12]}.")
        if prepare is not None:
            try:
                prepare(pending)
            except (UpdateError, OSError, subprocess.SubprocessError) as stop_err:
                log(f"Warning while stopping services for rollback: {stop_err}")
        self.git("reset", "--hard", pending.previous)
        if reinstall:
            install()
        restart()
        if verify is not None:
            try:
                verify()
            except (UpdateError, OSError, subprocess.SubprocessError) as verify_err:
                log(f"Warning: rollback readiness check failed: {verify_err}")
        self.pending_path.unlink(missing_ok=True)

    def finish(
        self,
        pending: PendingUpdate,
        install: Callable[[], None],
        restart: Callable[[], None],
        *,
        prepare: Callable[[PendingUpdate], None] | None = None,
        verify: Callable[[], None] | None = None,
        rollback_on_failure: bool = False,
    ) -> None:
        install_started = pending.installed
        try:
            if not pending.installed:
                pending.stage = "installing"
                self.save_pending(pending)
                install_started = True
                install()
                pending.installed = True
                self.save_pending(pending)
            pending.stage = "starting"
            self.save_pending(pending)
            restart()
            pending.stage = "verifying"
            self.save_pending(pending)
            if verify is not None:
                verify()
        except (UpdateError, OSError, ValueError, subprocess.SubprocessError) as exc:
            if not rollback_on_failure:
                raise
            self.save_failed(pending.target)
            try:
                self._rollback(
                    pending,
                    install,
                    restart,
                    prepare=prepare,
                    verify=verify,
                    reinstall=install_started,
                )
            except (UpdateError, OSError, ValueError, subprocess.SubprocessError) as rb_exc:
                raise UpdateError(
                    f"Update to {pending.target[:12]} failed ({exc}); rollback also failed "
                    f"({rb_exc}). Review {self.pending_path}."
                ) from exc
            raise UpdateError(
                f"Update to {pending.target[:12]} failed ({exc}); rolled back to "
                f"{pending.previous[:12]} and skipping this commit until upstream advances."
            ) from exc
        if self.git("rev-parse", "HEAD") != pending.target:
            raise UpdateError("HEAD changed during update completion; review the pending update.")
        self.require_clean()
        self.pending_path.unlink()
        self.clear_failed()
        log(
            "Iris update completed."
            if verify is None
            else "Iris update completed and readiness checks passed."
        )


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


class HealthProbe:
    """Authenticated, bounded readiness without logging keys or response bodies."""

    def __init__(
        self,
        url: str,
        *,
        kind: str = "iris",
        token_env: str = "IRIS_DROID_TOKEN",
        timeout: float = 180,
        stable: float = 3,
        ca: Path | None = None,
    ) -> None:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise UpdateError("Use an HTTP(S) health URL without credentials, query or fragment.")
        token = os.environ.get(token_env, "").strip()
        if not token or any(ch in token for ch in "\r\n"):
            raise UpdateError(f"Set {token_env} in the updater environment for readiness checks.")
        self.url, self.kind, self.timeout, self.stable = url, kind, timeout, stable
        self.headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=ca)),
        )

    def wait(
        self,
        running: Callable[[], bool] = lambda: True,
        instance: Callable[[], str | None] = lambda: None,
    ) -> None:
        deadline = time.monotonic() + self.timeout
        healthy_since: float | None = None
        while True:
            if not running():
                raise UpdateError("The managed process exited before becoming ready.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise UpdateError("Application readiness timed out; the update remains pending.")
            try:
                request = urllib.request.Request(
                    self.url, data=b"{}" if self.kind == "home" else None, headers=self.headers
                )
                with self.opener.open(request, timeout=min(2, remaining)) as response:
                    body = response.read(65537)
                    payload = json.loads(body) if len(body) <= 65536 else None
                ready = isinstance(payload, dict) and (
                    payload.get("status") == "ready"
                    if self.kind == "iris"
                    else isinstance(payload.get("scheduler"), dict)
                    and payload["scheduler"].get("state") == "ready"
                )
                expected = instance()
                ready = ready and (expected is None or payload.get("instance") == expected)
            except (OSError, ValueError, urllib.error.URLError):
                ready = False
            if ready:
                healthy_since = healthy_since if healthy_since is not None else time.monotonic()
                if time.monotonic() - healthy_since >= self.stable and running():
                    return
            else:
                healthy_since = None
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))


class ManagedServices:
    """Quiesce the original active/failed set and retain it across interrupted updates."""

    def __init__(self, names: list[str], save: Callable[[PendingUpdate], None]) -> None:
        self.names = list(dict.fromkeys(names))
        self.save = save
        self.selected: list[str] = []

    @staticmethod
    def state(name: str) -> dict[str, str]:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                "--property=LoadState,ActiveState,MainPID,NRestarts",
                "--",
                name,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if fields.get("LoadState") == "not-found":
            return fields
        result.check_returncode()
        return fields

    def prepare(self, pending: PendingUpdate) -> None:
        if pending.services is None:
            pending.services = [
                name
                for name in self.names
                if self.state(name).get("ActiveState")
                in {"active", "activating", "reloading", "failed"}
            ]
            self.save(pending)
        if not set(pending.services) <= set(self.names):
            raise UpdateError(
                "Pending services differ from this updater configuration; review them first."
            )
        self.selected = pending.services
        for name in self.selected:
            subprocess.run(
                ["systemctl", "--user", "stop", "--", name],
                stdin=subprocess.DEVNULL,
                timeout=60,
                check=True,
            )
            if self.state(name).get("ActiveState") not in {"inactive", "failed"}:
                raise UpdateError("A managed service did not stop; dependencies were not changed.")

    def start(self) -> None:
        failures = []
        for name in self.selected:
            try:
                subprocess.run(
                    ["systemctl", "--user", "start", "--", name],
                    stdin=subprocess.DEVNULL,
                    timeout=60,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError):
                failures.append(name)
        if failures:
            raise UpdateError("Managed services failed to start: " + ", ".join(failures))

    def verify(self, *, stable: float = 3, timeout: float = 30) -> None:
        if not self.selected:
            log("No previously active or failed services required restarting.")
            return
        deadline = time.monotonic() + timeout
        previous = None
        healthy_since: float | None = None
        while time.monotonic() < deadline:
            states = [self.state(name) for name in self.selected]
            if all(
                s.get("ActiveState") == "active" and s.get("MainPID", "0") != "0" for s in states
            ):
                identity = [(s.get("MainPID"), s.get("NRestarts")) for s in states]
                if identity != previous:
                    previous, healthy_since = identity, time.monotonic()
                if healthy_since is not None and time.monotonic() - healthy_since >= stable:
                    log(
                        "Managed service processes remained active; "
                        "hardware acceptance is separate."
                    )
                    return
            else:
                previous, healthy_since = None, None
            time.sleep(0.25)
        raise UpdateError("Managed service readiness failed; the update remains pending.")


class WindowsServer:
    def __init__(self, repo: Path, launcher: Path, powershell: str) -> None:
        self.repo = repo
        self.launcher = launcher
        self.powershell = powershell
        self.process: subprocess.Popen[bytes] | None = None
        self.instance: str | None = None

    def start(self) -> None:
        self.instance = uuid4().hex
        self.process = subprocess.Popen(
            [self.powershell, "-NoProfile", "-File", str(self.launcher)],
            cwd=self.repo,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            env={**os.environ, "IRIS_INSTANCE_ID": self.instance},
        )
        code = self.wait(timeout=2)
        if code is None:
            log("Started the Iris server.")
            return
        raise UpdateError(f"The Iris launcher exited during startup (exit code {code}).")

    def wait(self, timeout: float) -> int | None:
        # A long Popen.wait() on Windows can defer Ctrl+C until the child exits or
        # the timeout expires. Poll briefly so the watcher can handle Ctrl+C.
        deadline = time.monotonic() + timeout
        while True:
            code = self.process.poll() if self.process is not None else None
            if code is not None:
                return code
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(0.25, remaining))

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        result = subprocess.run(
            ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        if result.returncode and self.process.poll() is None:
            raise UpdateError("Could not stop the Iris launcher and its child processes.")
        self.process.wait(timeout=10)

    def restart(self) -> None:
        self.stop()
        self.start()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--service", action="append", help="systemd user service to restart (repeatable)"
    )
    mode.add_argument("--launcher", type=Path, help="PowerShell server launcher to manage")
    mode.add_argument(
        "--check-access", action="store_true", help="fetch without applying an update"
    )
    parser.add_argument("--powershell", default="powershell.exe")
    parser.add_argument(
        "--health-url", help="readiness URL; Windows defaults to localhost:8765/v1/health"
    )
    parser.add_argument("--health-kind", choices=("iris", "home"), default="iris")
    parser.add_argument("--health-token-env", default="IRIS_DROID_TOKEN")
    parser.add_argument("--health-ca", type=Path, help="trusted CA for an HTTPS readiness endpoint")
    parser.add_argument("--readiness-timeout", type=float, default=180)
    parser.add_argument(
        "--install-command", nargs=argparse.REMAINDER, help="optional command, last"
    )
    args = parser.parse_args()
    if args.launcher and os.name != "nt":
        parser.error("--launcher requires Windows")
    if args.launcher and args.health_kind != "iris":
        parser.error("--launcher requires --health-kind iris")
    if not 8 <= args.readiness_timeout <= 600:
        parser.error("--readiness-timeout must be between 8 and 600 seconds")
    if args.install_command == []:
        parser.error("--install-command needs a command")

    server = None
    try:
        repo = Checkout(args.repo.resolve())
        with updater_lock(repo.lock_path):
            if args.check_access:
                target = repo.fetch_upstream()
                log(f"Git access works. Upstream is at {target[:12]}.")
                return 0

            probe = (
                HealthProbe(
                    args.health_url or "http://127.0.0.1:8765/v1/health",
                    kind=args.health_kind,
                    token_env=args.health_token_env,
                    timeout=args.readiness_timeout,
                    ca=args.health_ca,
                )
                if args.launcher or args.health_url
                else None
            )

            def install() -> None:
                if args.install_command:
                    log("Running the Iris dependency installation command.")
                    try:
                        subprocess.run(
                            args.install_command,
                            cwd=repo.path,
                            stdin=subprocess.DEVNULL,
                            timeout=600,
                            check=True,
                        )
                    except (OSError, subprocess.SubprocessError):
                        raise UpdateError(
                            "Dependency installation failed; the update remains pending."
                        ) from None

            if args.service:
                services = args.service
                # Older installed updater units pass only the default client name.
                if "iris-client.service" in services:
                    services = [*services, "iris-home.service"]
                managed = ManagedServices(services, repo.save_pending)

                def verify_services() -> None:
                    managed.verify()
                    if probe is not None and managed.selected:
                        probe.wait()

                repo.update(
                    install,
                    managed.start,
                    prepare=managed.prepare,
                    verify=verify_services,
                    rollback_on_failure=True,
                )
                return 0

            launcher = (repo.path / args.launcher).resolve(strict=True)
            server = WindowsServer(repo.path, launcher, args.powershell)
            assert probe is not None

            def verify_server() -> None:
                try:
                    probe.wait(
                        lambda: server.process is not None and server.process.poll() is None,
                        lambda: server.instance,
                    )
                except BaseException:
                    server.stop()
                    raise

            try:
                while True:
                    try:
                        repo.update(
                            install,
                            server.restart,
                            prepare=lambda _: server.stop(),
                            verify=verify_server,
                            rollback_on_failure=True,
                        )
                    except (UpdateError, OSError, ValueError, subprocess.SubprocessError) as exc:
                        log(f"Iris update skipped or incomplete: {exc}")
                    if not repo.pending_path.exists() and server.process is None:
                        server.start()
                        verify_server()
                    code = server.wait(timeout=300)
                    if code is None:
                        continue
                    if repo.pending_path.exists():
                        time.sleep(300)
                        continue
                    log(f"The Iris server exited (exit code {code}).")
                    return code
            except KeyboardInterrupt:
                log("Stopping the Iris updater and server.")
                return 0
            finally:
                server.stop()
    except KeyboardInterrupt:
        return 0
    except (UpdateError, OSError, ValueError, subprocess.SubprocessError) as exc:
        log(f"Iris update skipped or incomplete: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
