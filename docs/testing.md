# Checking a change to Iris

Most of Iris can be tested without talking to a model, recording anything or
switching a real device. That's the quickest way to check a change before trying
it on the robot. You'll still need localhost access for the tests that start a
small HTTP server.

Install the development tools in your Iris environment:

```sh
python -m pip install -e ".[dev]"
```

If you're using uv, `uv sync --locked --extra dev` installs the locked versions.
Use `uv run --no-sync` before the Python commands below in that environment.

## The everyday checks

```sh
ruff check .
ruff format --check .
mypy
python scripts/check.py
```

The check script runs pytest from the repository directory, even if you start it
somewhere else. It passes pytest arguments through, so you can select a file or
use `-k` while working on one feature. It doesn't install anything or run lint
and type checks for you.

The [compatibility reference](compatibility.md) describes behaviour worth keeping
steady during a refactor. `tests/support.py` provides a fake clock, scripted model,
recogniser, synthesiser and switch. Use these when a test needs a controllable
boundary; don't replace a real protocol or database with a mock when that is the
part you're testing. The scripted model rejects an unplanned request unless the
test deliberately supplies a fallback.

## Coverage and known defects

The [verification map](verification.md) connects features, configuration families
and invariants to tests and physical acceptance. The generated
[settings reference](configuration-reference.md) is checked by the documentation
tests; regenerate it with `python scripts/build_reference.py` after changing
declarations or help. Runnable extension examples cover chat, controls, tools,
speech and devices.

The test fixture removes inherited `IRIS_*` configuration and credentials before
each test, preserving explicit `IRIS_TEST_*` opt-ins. A test must set its own
synthetic keys/endpoints. This does not replace transport fakes: never point a
test at a real home service or personal archive. Local HTTP tests bind port zero
so the OS allocates a free port. Intentional stall tests use bounded subprocesses;
deadline/race tests should prefer events or injected clocks over arbitrary sleeps.

```sh
python scripts/check.py --coverage
```

This measures statements and branches, prints a per-file report and writes
`.cache/checks/coverage.json` and `.cache/checks/coverage.md`. The Markdown report
groups results by subsystem. `python -m coverage html` gives you a browsable report
with the missed lines highlighted.

We don't currently enforce an arbitrary percentage. Compare the same test command,
platform and optional features when judging a change. A covered branch can still
be wrong, and native model workers run in separate interpreters: this report does
not claim to measure their execution. Tests for those adapters use fake models.

The review regressions now pass normally. Run that original set with:

```sh
python scripts/check.py -m review_regression --artifacts
```

Include `--browser` to exercise the browser personality regression too. Additional
failure checks live in `test_phase1_boundaries.py` and `test_phase1_persistence.py`.
They cover upload/admission limits, deadlines, stream completion and framing,
archive backlog recovery, migration rollback and interrupted configuration writes.

If a future confirmed defect temporarily needs an expected-failure test, use a
strict marker restricted to its specific defect assertion. Setup errors must
still fail normally. An unexpected pass must fail CI until the marker is removed.
Use `--runxfail` to check the actual failure and `-rx` to see recorded reasons.
Finding IDs are stable labels; each regression explains the behaviour without
depending on the maintainer's local review notes.

## Check the installed wheel

```sh
python scripts/check.py --artifacts tests/artifacts
```

These checks copy the package sources into a temporary build directory, build a
source distribution, build a wheel from its extracted contents and install it into a fresh target directory without downloading packages.
An isolated Python process imports the installed code, checks command help and
reads packaged assets. It disables site initialization, editable-install hooks,
the working directory and `PYTHONPATH`. Third-party dependencies come from the
prepared test environment; Iris itself must come from the new installation.

This catches missing files that an editable install can hide. It isn't a test of
dependency resolution on a completely clean machine. The server and client
wizards, other installed entry points and selected resources must all pass.

## JavaScript and browser checks

Use the Node version in `.node-version` and run `npm ci --ignore-scripts`, then
`npm test`. This checks module syntax, pure JavaScript contracts and regenerated
face pixels without altering the shipped artwork. [Contributing](../CONTRIBUTING.md)
explains the native-renderer tolerance. These checks complement the real browser
suite below.


Prepare the browser once, while you have a network connection:

```sh
python -m playwright install chromium
```

Then run:

```sh
python scripts/check.py --browser tests/web
```

The tests execute the actual JavaScript modules in Chromium, including speech
segmentation and WAV conversion. Browser smoke tests load the real HTML, CSS and
scripts with intercepted responses. A generated audio signal supplies a synthetic
microphone stream and the reply contains a synthetic WAV. They never open the
physical microphone, use your connection key or contact your Iris server.

Browser checks are opt-in locally, so ordinary Python tests don't unexpectedly
launch or download a browser. CI runs them in a separate job. Set
`PLAYWRIGHT_BROWSERS_PATH` for both installation and testing if you want to keep
the browser download in a particular cache directory. You can also set
`IRIS_TEST_BROWSER_CHANNEL=chrome` to exercise an installed Chrome, but use the
matching bundled Chromium when comparing the CI baseline.

The harness follows [Playwright's Python library setup](https://playwright.dev/python/docs/library).
Coverage settings follow the [coverage.py configuration reference](https://coverage.readthedocs.io/en/latest/config.html).

## Compare local overhead

```sh
python scripts/benchmark.py --exchanges 1000 --turns 10
```

This creates a temporary synthetic archive and exercises text, buffered voice
and streamed voice turns with fake inference and audio. It records turn-stage
times, SQLite connection and statement counts, several archive-query timings,
and query plans in `.cache/checks/benchmark.json`. It never opens your saved archive.

Run the same command on the same machine before and after a change, preferably
several times. Stage times can overlap: memory preparation includes checkpoint
work, for example. The three modes run sequentially against a growing archive,
so their numbers aren't a controlled comparison of the modes themselves. Small
sample p95 values are descriptive, not performance guarantees.

This measures Python orchestration and SQLite overhead. It says nothing about
real model speed, speech quality, network latency or Pi audio. Use a larger
`--exchanges` value to investigate archive growth, and choose `--output` if you'd
like to keep two results for comparison. There is no timing threshold in CI.
SQLite statement counts come from trace callbacks, including trigger and internal
activity; they aren't counts of distinct application queries. The synthetic model
returns empty summaries, so this isn't a benchmark of a populated semantic index.

## Before calling a release ready

Python, artifact and browser checks can catch a lot, but they can't establish
that a real microphone hears quiet speech or that a device accepts a Matter
command. Try the affected paths on the hardware you use. Pay particular attention
to wake and cancellation recognition, playback starting cleanly, touch controls,
service startup after reboot, and updates on Windows. Keep those results separate
from automated test results.

Application and client boundaries are described in [the architecture guide](architecture.md).
`tests/test_application_contracts.py` exercises coordination, rollback, effect
policy and resource ownership without starting an HTTP server or opening devices.

## Memory migrations and recovery

`tests/fixtures/memory/v1.sql` through `v9.sql` are frozen synthetic archives,
including original Unicode text, multiple owners and the state available at each
version. Tests load these SQL files directly; they don't construct an old schema
by running today's migrations and lowering its version number. When adding a
migration, keep the old fixtures and add a fixture for the new version.

`tests/test_phase3_memory.py` checks every supported version, interruption after
each migration, future-version refusal, consistent read snapshots, restartable
maintenance, stale inference rejection and stopped backup/restore/rebuild. The
large-archive case also records the semantic candidate horizon without changing
retrieval policy or throwing away old sources. Existing hierarchy and persistence
tests cover correction independence, deletion scopes and voice acceptance.

To measure actual query plans and warmed read timings on disposable data:

```sh
python scripts/benchmark_memory.py --exchanges 10000 --repetitions 20
```

The JSON result goes to `.cache/checks/memory-benchmark.json`. It reports Python
allocation peaks separately from timings and excludes real model inference. Run
benchmarks without the full suite competing for CPU if you want useful timing
comparisons. This is a diagnostic benchmark, not a machine-specific CI speed gate.

See [performance and recovery](performance-and-recovery.md) for redacted stage
timings, health checks, speech-worker recovery, scheduler ordering and reproducible
synthetic benchmarks.

## Documentation and dependency profiles

The ordinary suite checks local Markdown files and anchors, executes the Python
extension recipes and checks the shared menu's command phrases against their
entry points. Run `python scripts/check_docs.py` for links alone. External URLs
still need human review; this check deliberately makes no network requests.

Build one wheel into an otherwise empty `dist` directory with `python -m build`.
Then `python scripts/check_clean_install.py base` creates a disposable virtual
environment outside the checkout, installs the wheel and dependencies, runs pip's
consistency check, verifies import origins and exercises installed command help.
Unlike the artifact fixture, this needs package-download access. Repeat with
`server`, `memory`, `vad` or `face` for those optional profiles. The face profile
also installs Pygame 2.6.1 and renders the shipped sprites and menus with SDL's
dummy display, so it doesn't need a desktop or open a window. See
[installation profiles](install-profiles.md) for native libraries and model limits,
and [release acceptance](releasing.md) for the checks on actual devices.

## Real speech without a microphone

Linux CI uses an explicit [ALSA null PCM](https://alsa-project.org/alsa-doc/alsa-lib/pcm_plugins.html)
configuration in `tests/fixtures/alsa-null.conf` so PortAudio can initialise
without a sound card or a logged-in desktop audio session. This is a silent test
device, not evidence of working input or output hardware. Package import failures
still fail the profile and include their traceback. Each matrix job runs to
completion even if another platform fails.

If you have Iris's `whisper,kokoro` extras installed, you can check the real CPU
models with a sentence generated entirely for the test. Choose a disposable
directory for the assets and results. From an installed checkout, run:

```sh
python scripts/check_local_speech.py --assets .cache/speech-check --prepare
python scripts/check_local_speech.py --assets .cache/speech-check
```

The first command downloads about 430 MB: the checksum-checked Kokoro pack and a
pinned revision of Whisper tiny.en. The second runs Kokoro, converts its output
to PCM and sends it through Iris's Whisper adapter. It checks for recognisable
words, finite non-silent audio and the expected sample rate. It writes a synthetic
WAV and `result.json` with versions, asset hashes, transcription and elapsed time.
Treat that time as a diagnostic observation, not a benchmark or quality score.

You can also exercise Pocket's real voice preparation and supervised worker.
Install the separate Pocket environment as described in [server setup](server-setup.md),
then add `--pocket-python .venv-pocket/bin/python` to **both** commands above.
On Windows use `--pocket-python .venv-pocket/Scripts/python.exe`. Preparation
downloads Pocket's pinned model as well, so allow roughly another 210 MB for those weights, plus the separate
worker environment. The check prepares a temporary voice from the synthetic Kokoro WAV and
transcribes Pocket's reply with Whisper. It never uses or replaces your prepared
voices. Both environments must be installed before running the commands.

The runner clears inherited Iris settings, uses its own Hugging Face cache and
disables implicit tokens. During inference, model loading is local-only and Python
socket connection attempts fail. The check runs in a child process with a ten-minute
limit. It doesn't read your Iris configuration or archive, start a chat server,
open a microphone or play sound. Native libraries aren't covered by Python's
socket hook, so this isn't a general network sandbox. Missing packages or assets
fail the command; they aren't quietly skipped. These checks are explicit opt-ins
because they download large assets and need additional environments.

For native graphics alone, install Iris and Pygame, then run
`python scripts/check_native_display.py`. This uses the real renderer in both
landscape and portrait, including sprite expressions, menu fonts and synthetic
touch events. It doesn't prove that the Pi's display or touchscreen works.
