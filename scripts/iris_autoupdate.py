"""Opt-in Git updates for an Iris checkout."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path


class UpdateError(Exception):
    pass


def log(message: str) -> None:
    print(message, flush=True)


@dataclass
class PendingUpdate:
    previous: str
    target: str
    installed: bool = False


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

    def save_pending(self, pending: PendingUpdate) -> None:
        temp = self.pending_path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(pending)), encoding="utf-8")
        temp.replace(self.pending_path)

    def update(self, install: Callable[[], None], restart: Callable[[], None]) -> None:
        current = self.git("rev-parse", "HEAD")
        if self.pending_path.exists():
            pending = PendingUpdate(**json.loads(self.pending_path.read_text(encoding="utf-8")))
            if current == pending.target:
                self.require_clean()
                self.finish(pending, install, restart)
                return
            if current != pending.previous:
                raise UpdateError(
                    f"HEAD moved during a pending update. Review {self.pending_path} "
                    "and complete the update manually."
                )
            self.pending_path.unlink()

        target = self.fetch_upstream()
        if current == target:
            return
        # rev-list returns counts for commits exclusive to each side.
        ahead, behind = map(
            int, self.git("rev-list", "--left-right", "--count", f"{current}...{target}").split()
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
        self.git("merge", "--ff-only", "--no-overwrite-ignore", target)
        log(f"Updated Iris from {current[:12]} to {target[:12]}.")
        self.require_clean()
        self.finish(pending, install, restart)

    def finish(
        self, pending: PendingUpdate, install: Callable[[], None], restart: Callable[[], None]
    ) -> None:
        if not pending.installed:
            install()
            pending.installed = True
            self.save_pending(pending)
        restart()
        self.pending_path.unlink()


class WindowsServer:
    def __init__(self, repo: Path, launcher: Path, powershell: str) -> None:
        self.repo = repo
        self.launcher = launcher
        self.powershell = powershell
        self.process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(
            [self.powershell, "-NoProfile", "-File", str(self.launcher)],
            cwd=self.repo,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
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


def restart_service(name: str) -> None:
    systemctl = ["systemctl", "--user"]
    state = subprocess.run(
        [*systemctl, "show", "--property=LoadState", "--value", "--", name],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if state.stdout.strip() == "not-found":
        return
    state.check_returncode()
    failed = (
        subprocess.run(
            [*systemctl, "is-failed", "--quiet", "--", name],
            stdin=subprocess.DEVNULL,
            timeout=10,
            check=False,
        ).returncode
        == 0
    )
    action = "restart" if failed else "try-restart"
    subprocess.run(
        [*systemctl, action, "--", name],
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=True,
    )
    log(f"Finished the update for {name}.")


def restart_services(names: list[str]) -> None:
    failures = []
    for name in dict.fromkeys(names):
        try:
            restart_service(name)
        except (OSError, subprocess.SubprocessError) as error:
            log(f"Could not restart {name}: {error}")
            failures.append(name)
    if failures:
        raise UpdateError(f"Services still need to restart: {', '.join(failures)}.")


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
        "--install-command", nargs=argparse.REMAINDER, help="optional command, last"
    )
    args = parser.parse_args()
    if args.launcher and os.name != "nt":
        parser.error("--launcher requires Windows")
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

            def install() -> None:
                if args.install_command:
                    if server:
                        server.stop()
                    log("Running the Iris dependency installation command.")
                    subprocess.run(
                        args.install_command,
                        cwd=repo.path,
                        stdin=subprocess.DEVNULL,
                        timeout=600,
                        check=True,
                    )

            if args.service:
                services = args.service
                # Older installed updater units pass only the default client name.
                if "iris-client.service" in services:
                    services = [*services, "iris-home.service"]
                repo.update(install, lambda: restart_services(services))
                return 0

            launcher = (repo.path / args.launcher).resolve(strict=True)
            server = WindowsServer(repo.path, launcher, args.powershell)
            try:
                while True:
                    try:
                        repo.update(install, server.restart)
                    except (UpdateError, OSError, ValueError, subprocess.SubprocessError) as exc:
                        log(f"Iris update skipped or incomplete: {exc}")
                    if not repo.pending_path.exists() and server.process is None:
                        server.start()
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
