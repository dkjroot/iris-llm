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
wheel and install it into a fresh target directory without downloading packages.
An isolated Python process imports the installed code, checks command help and
reads packaged assets. It disables site initialization, editable-install hooks,
the working directory and `PYTHONPATH`. Third-party dependencies come from the
prepared test environment; Iris itself must come from the new installation.

This catches missing files that an editable install can hide. It isn't a test of
dependency resolution on a completely clean machine. The server and client
wizards, other installed entry points and selected resources must all pass.

## JavaScript and browser checks

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
