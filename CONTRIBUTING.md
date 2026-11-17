# Working on Iris

Iris has grown out of a working robot, so a useful change needs to hold up in an
actual conversation as well as in a test. The aim is to keep it easy to add a
model, client or skill without making every part depend on every other part.

Use Python 3.11, 3.12 or 3.13 in a virtual environment. From a checkout, install
the development environment with the locked dependencies:

```sh
python -m pip install uv==0.7.19
uv sync --locked --extra dev
uv run --no-sync python scripts/check.py --artifacts
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy
```

These tests use synthetic conversations, local HTTP servers and fake models.
They don't need your connection key or your archive. If you prefer pip,
`python -m pip install -e ".[dev]"` also works, but resolves the compatible
versions available at installation time rather than using `uv.lock`.

Start with [the architecture](docs/architecture.md) and
[the runtime contracts](docs/runtime-contracts.md). The
[extension recipes](docs/extensions.md) show how to add a backend, exact command
or model-selected tool, including small tests you can run. For a client change,
check both the Pi controller and browser controller; the wire protocol and
shared command list belong to both.

## Pick the checks that exercise the change

The [testing guide](docs/testing.md) explains the suite, coverage, browser checks,
installed packages and synthetic benchmarks. Tests should check the behaviour at
the boundary you are changing. Use a real temporary SQLite database for storage
contracts, and a local HTTP server for HTTP framing. Use controlled fake models
and devices when their responses, delays or failures are the input to the test.

When adding a voice control, update `iris_droid/web/commands.json` and the relevant
guide. Both menus use that file. Include exact phrase recognition, disabled
capabilities, text/voice differences and cancellation in the tests. A command
mentioned in a menu should actually work in the circumstances its description
promises.

Keep complete original non-private exchanges. Summaries, embeddings and notes
are derived views, and context limits must not turn into archive expiry. Changes
to persistence need migration, interrupted migration, backup/restore, privacy and
provenance tests. Never test against your only copy of a real archive.

## Browser code and face artwork

Node is a development tool here; the Pi runs the committed assets. Install the
Node version in `.node-version`, then run:

```sh
npm ci --ignore-scripts
npm test
```

`package-lock.json` pins the native canvas renderer and its platform packages.
`npm test` checks every browser module's syntax, exercises the pure speech and
request-state code, and regenerates the faces in memory for comparison. It does
not rewrite the committed artwork. The comparison requires identical dimensions
and manifest data. It allows small native-renderer rounding differences: at most
4/255 in any premultiplied colour or alpha channel, with an average below 0.02/255
across all channels. This is a visual-equivalence check across platforms, not a
claim of identical PNG bytes. Review intentional artwork changes visually too.

To write new artwork after changing its source, run `npm run build:faces`. To
compare two builds without touching the shipped files, use
`node scripts/build_face_sprites.cjs --output PATH` twice with separate paths.
Never download a model or regenerate the faces just to run the Python tests.
The [face guide](docs/face-display.md) explains the display itself.

## Before sharing a change

Explain the problem, what someone will notice, and the checks you ran. Mention
unverified hardware paths plainly. Keep secrets, recordings, private configuration
and conversation exports out of patches and test fixtures. Use synthetic strings
for privacy tests, and check that the strings do not escape through errors or
traces as well as through normal responses.

Follow the existing module boundaries rather than adding another forwarding
layer just to move lines around. A comment is most useful when it explains a
constraint, ordering rule or decision that the code alone doesn't make clear.

The [release checklist](docs/releasing.md) covers clean installs, update recovery
and the separate PC, Pi and phone acceptance session. The
[installation profiles](docs/install-profiles.md) distinguish Python dependencies,
native libraries and downloaded model files.
