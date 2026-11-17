# Optional automatic updates

If you're following development and want to keep Iris up to date, you can run a
separate updater on the PC, the Pi, or both. You can still pull changes and
start Iris yourself as usual. The updater checks your current branch's Git
upstream every five minutes and applies new commits when it can fast-forward
the checkout. If you have tracked edits or the branches have diverged, it
leaves the checkout for you to sort out. An update restarts Iris and can
interrupt a conversation, so use a branch you're happy to run as it changes.

On the Pi, first set up `iris-client.service` in [client setup](client-setup.md#7-start-the-pi-client-at-boot). Then copy the updater
units from your Iris checkout and open the installed service file:

```sh
cp systemd/iris-autoupdate.{service,timer} ~/.config/systemd/user/
nano ~/.config/systemd/user/iris-autoupdate.service
```

Set `WorkingDirectory` to your checkout. The supplied service uses
`/usr/bin/python3`. If you'd rather run the updater with Python from your
virtual environment, change the first path in `ExecStart` as well. For
example, if your checkout is at `~/git/iris-llm` and its virtual environment
is `.venv`, these are the two lines to set under `[Service]`:

```ini
WorkingDirectory=%h/git/iris-llm
ExecStart=%h/git/iris-llm/.venv/bin/python scripts/iris_autoupdate.py --service iris-client.service --service iris-home.service
```

`%h` stands for your home directory. Use the actual path to your Python
executable if you named the environment differently. You can also change the
names after `--service` if your Iris units have different names. Then enable
the timer:

```sh
systemctl --user daemon-reload
systemctl --user enable --now iris-autoupdate.timer
systemctl --user list-timers iris-autoupdate.timer
```

The timer starts the same one-shot service you can start by hand. To check
now and see the result, run:

```sh
systemctl --user start iris-autoupdate.service
journalctl --user -u iris-autoupdate.service -n 30
```

A manual run applies any available update just like a scheduled run. The
updater asks systemd to restart the voice client and optional home service
if they're running or failed. The updater service goes inactive when the
check finishes, and the timer stays active. If the logs are empty, see
[the Pi journal setup](client-setup.md#if-raspberry-pi-os-only-shows-your-service-logs-with-sudo).
To turn off scheduled checks, run
`systemctl --user disable --now iris-autoupdate.timer`.

On Windows, stop the server you started by hand, open PowerShell in the Iris
checkout, and run the watcher. Give it the name of your local launcher:

```powershell
py -3 scripts/iris_autoupdate.py --launcher .\run_server.ps1
```

Use `start_server.ps1` instead if that's what you called it. The launcher
should run the server in the foreground and set up any environment and
arguments the server needs. Leave the watcher shell open. It starts the
launcher, replaces the server process after updates, and stops the server
when you press Ctrl+C. If your launcher needs PowerShell 7, add
`--powershell pwsh.exe`. If an update changes the watcher itself, close and
reopen the watcher to pick up its new code.

Git uses your normal user credentials. Public HTTPS remotes can be fetched
without signing in. For a private remote, Git needs credentials it can use
without prompting. On the Pi, an SSH agent available in your terminal may
not be available to the user service. To check Git access without applying
changes, run the updater with `--check-access` in place of `--launcher` or
`--service`. On the Pi, you can run that check in a transient user service
from your checkout with
`systemd-run --user --wait --pipe --working-directory="$PWD" /usr/bin/python3 scripts/iris_autoupdate.py --check-access`.

## Optional Python package updates

The basic updater pulls changes to the Iris code and restarts Iris. It uses
the Python packages already installed in your environment. If a change needs
a new package or version, you can stop Iris, run your usual install command,
and start it again. If you'd like to automate that step too, pick one of the
two setups below.

**Run pip whenever you start Iris.** Add this block to your existing
`run_server.ps1`, just before the line that starts Iris. This example assumes
the script and the server's `.venv` are in the Iris checkout. `$PSScriptRoot`
is the folder containing the script, so pip can find the checkout even if
you start the script from another folder.

```powershell
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python -m pip install -e "${PSScriptRoot}[whisper,kokoro]"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
# Keep your existing command that starts Iris below this block.
```

With this setup, pip runs every time the launcher starts, whether you run it
by hand or the watcher runs it after an update. The check after pip stops the
script if the install fails. Keep using the short watcher command with
`--launcher .\run_server.ps1`.

**Run pip only after the watcher pulls an update.** Keep your existing
`run_server.ps1` as it is. Put this line in the script you use to start the
watcher, and run it from the Iris checkout:

```powershell
py -3 scripts/iris_autoupdate.py --launcher .\run_server.ps1 --install-command .\.venv\Scripts\python.exe -m pip install -e ".[whisper,kokoro]"
```

PowerShell starts the watcher. The watcher saves everything after
`--install-command` as the command to run later, which is why that option
goes last. It stops Iris before applying new commits, then runs pip and starts
Iris again through `run_server.ps1`. If the install or restart fails, it logs
the failure and retries on the next check.

Both examples use the server's `.venv` and the usual Whisper and Kokoro
extras. Keep any other extras you already use, such as `memory` for semantic
recall. If you installed with uv, use your usual `uv sync` command in place
of pip. On the Pi, you can add `--install-command` and your install command
at the end of `ExecStart` in the installed updater unit, then reload systemd.
Use the full path to the executable if the updater can't find it on its PATH.


## Readiness and saved update state

The updater now stops its managed processes before changing code or dependencies.
It records the previous and target commits in Git's `iris-autoupdate/pending.json`,
then records installation, startup and verification separately. A successful pip
command alone does not complete the update. On the Pi, it also saves which units
were active or failed before stopping them, so an interrupted run can restore the
same set. Missing and intentionally inactive units stay stopped. The saved service
names must still be present in the updater's configuration when it resumes.

For Windows, set `IRIS_DROID_TOKEN` in the shell or script that starts the watcher,
not only inside `run_server.ps1`. Use the same connection key as the server. A
child PowerShell process cannot set a variable back in its parent watcher. Load
the key through your usual local secret setup; do not add it to a Git-tracked script.
The default probe is `http://127.0.0.1:8765/v1/health`. If your server uses a different
port or TLS, pass `--health-url` and, for your own certificate authority,
`--health-ca PATH`. Certificate verification stays enabled.

The watcher supplies a fresh `IRIS_INSTANCE_ID` to each launcher. The server echoes
that opaque identifier only in its authenticated health result. The probe requires
the matching identifier, a live launcher and three continuous seconds of ready
responses, so an unrelated old server on that port cannot satisfy the new start.
Your launcher must keep the server in the foreground and preserve its environment.
Allow enough time for model loading with `--readiness-timeout SECONDS` (3–600,
default 180). Each health request uses a short I/O timeout, bounded response size,
no proxy and no redirects; response bodies and tokens are not printed.

The systemd check requires the original service set to remain active with stable
main process IDs and restart counters for three seconds, within a 30-second wait.
That detects immediate failures and restart loops; it cannot establish microphone,
model or physical device readiness. You can add an authenticated application probe,
for example `--health-kind home --health-url https://HOST:PORT/v1/list
--health-token-env IRIS_HOME_TOKEN --health-ca PATH`, with the home token available
to the updater's service environment. Use your actual configured URL and token
variable. The home probe requires the scheduler to report ready. Finish with the
[device acceptance checklist](releasing.md), whichever probes you use.

## Recovering an interrupted update

Leave pending state in place after an install, startup or readiness failure. Fix
the reported problem and rerun the same updater with the same configuration. If
HEAD still matches the recorded previous or target commit, it resumes the work;
a recorded successful install is not repeated. A process may remain stopped after
a failure, particularly if stopping, installation or a local-file collision failed.
The updater deliberately does not guess at a rollback of dependencies or databases.

If you see **HEAD moved during a pending update**, stop the watcher or Pi update
timer before investigating. This can happen when a manual `git pull` advances the
checkout beyond the target of an unfinished update, even when `git status` is clean.
A clean worktree says nothing about which dependencies were installed or which
server process is still running.

1. Record `git rev-parse HEAD`, `git status --short --branch` and the pending file.
   Use `git rev-parse --git-path iris-autoupdate/pending.json` to find it in a linked
   worktree. Inspect `git log --oneline PREVIOUS..HEAD` using the recorded commit.
2. Decide which commit you intend to run. Preserve local work and verify backups
   before switching versions. Do not reset a branch merely to silence the warning.
3. With the managed Iris processes stopped, run the installation command for that
   commit and your actual extras. Check that it succeeds. If the launcher itself
   installs packages, check its exit handling as described above.
4. Start that version manually, check its health and test a synthetic conversation.
   On the Pi, restore the active/failed service set recorded in the pending file.
   Check each unit, including the home service if it was running.
5. Only after those checks pass, rename the pending file to a dated recovery copy
   in the same local state directory. This preserves the evidence while letting
   the updater start a fresh cycle. Stop the manually started Windows server
   before restarting its watcher so the watcher owns the replacement process.

If a saved pending file is malformed, review it against the same procedure instead
of deleting it blindly. Old files containing only `previous`, `target` and
`installed` remain supported. A software rollback may also require a matching
archive backup: see [memory storage](memory-storage.md). Independent backups can
still contain previously forgotten information, so treat them as private copies.

[Back to Iris](../README.md) · [All guides](index.md)
