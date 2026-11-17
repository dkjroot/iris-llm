# Checking a release on the machines that run Iris

Use this checklist after the automated checks, while you have time to listen and
try the controls. A passing Python suite cannot tell us whether the Pi hears a
quiet voice, the phone trusts the certificate or the Windows launcher leaves an
old server behind. Record a result for each item: passed, failed with a short
sanitized description, or not applicable with a reason. An empty box is still
untested.

## Record the setup first

- [ ] Record the Iris commit on the PC and Pi with `git rev-parse HEAD`, plus
  `git status --short --branch`. Record the phone/browser version too.
- [ ] Record OS/architecture, Python version, chosen extras, local model host and
  model names, recognition/synthesis engine and CPU/GPU mode. Leave out keys,
  tokens, personal addresses and real conversation text.
- [ ] Note the microphone, speaker, mixer and display being tested. Identify one
  harmless home device, such as a lamp, that is safe to switch during the session.
- [ ] Stop Iris and make verified backups of the conversation archive and home
  schedules using [memory storage](memory-storage.md) and
  [home automation](home-automation.md). Use synthetic conversations for new checks.
- [ ] Keep a copy of the working configuration and launcher outside the checkout.
  Confirm that the old software can read the backup's schema before planning a
  rollback. Schema 9 archives need schema-9-aware software; do not point an older
  release at a newer archive and hope it works.

## Automated and clean-install checks

For a first installation, use [the full platform routes](start-here.md), not just
this checklist. The [verification map](verification.md) distinguishes automated
proof from the physical checks below.

- [ ] In a clean checkout, install the locked development environment and run
  Ruff, formatting, mypy and `python scripts/check.py --coverage --artifacts`.
- [ ] Run `npm ci --ignore-scripts` and `npm test` with `.node-version`'s Node.
- [ ] Install Playwright Chromium and run `python scripts/check.py --browser tests/web`.
- [ ] Review the Windows, Linux and macOS CI results for this exact commit,
  including the dependency-profile jobs. A configured job is not a passed job.
- [ ] Build with `python -m build`. In a fresh virtual environment outside the
  checkout, install the wheel with the extras you use, run `python -m pip check`,
  and exercise the installed command help. Do not let an editable install mask
  missing files. Repeat for the platforms/profiles the release claims to support.
- [ ] Extract the source distribution and run its documentation/profile checks.
  Check that it includes the guides, scripts, service templates and JavaScript
  lock, and excludes local configuration, archives, credentials and working notes.

## First-time installation and documentation acceptance

Do these on spare storage/clean environments so a working installation remains
available. Record each route separately; one successful PC doesn't validate
another operating system or accelerator.

- [ ] Follow the Windows 11 guide through the chosen Pi, phone, Siri and Matter
  chapters, then finish host startup, reboot and restore. Record the exact build,
  Python, model/runtime files, GPU backend and all optional omissions.
- [ ] Repeat with the macOS Apple Silicon route, including launchd login startup,
  microphone permissions and the selected local speech/model environments.
- [ ] Repeat with Ubuntu 24.04 LTS, including native libraries, firewall and user
  services without an open terminal.
- [ ] Record the Pi OS image, touchscreen/audio identities, device firmware and
  chip-tool revision for each complete route. Reuse is fine if stated; it is not
  a second clean-Pi install result.
- [ ] Have a reader new to the project follow the installation without filling
  gaps for them. Record the first unclear instruction, what they expected, and
  what happened. Repair it and repeat that milestone.
- [ ] Have a reader without specialist background follow the technical guide.
  They should be able to explain where models run, how a spoken turn moves,
  what privacy/retention mean, and why accepted, saved and delivered differ.
  Record unexplained terms or missing prerequisites and repair the explanation.

## Windows PC and local models

- [ ] Start your local model host and Iris with your normal launcher. Confirm a
  text question works, then a voice question uses the intended local recognition
  and synthesis models. Check that no fallback provider was enabled accidentally.
- [ ] With models already downloaded, try the local conversation while internet
  access is unavailable but the PC/Pi LAN connection remains available. Weather
  and notifications are online features and should fail clearly in that case.
- [ ] Check the authenticated `/v1/health` result. An incorrect or missing key
  must fail. Remember that `ready` does not prove the next model call will succeed.
- [ ] Speak several short and long replies. Listen for clipped openings, clicks,
  gaps or failed playback; try each synthesis engine you intend to ship as tested.
- [ ] Stop and restart Iris. Check that a synthetic saved conversation survives,
  and that a private conversation does not appear in the archive. Test explicit
  confirmed forgetting using a disposable conversation, then restart and check again.

## Pi voice, face and controls

- [ ] Boot the Pi and confirm the configured client and optional home service
  start under the intended account. Check their user-service logs.
- [ ] Test Enter-to-talk, the wake phrase, a follow-up, “Stop listening”, and
  “Cancel, cancel” while Iris is thinking and while a reply is playing. A cancelled
  provisional turn must not become a saved exchange. An external action already
  attempted may still have happened; check it before asking for it again.
- [ ] Check listening, working, clear-context and cancellation sounds. Repeat with
  `--no-sounds`: optional feedback goes quiet, but voice-cancel acknowledgement
  still plays. Text mode stays silent.
- [ ] Check quiet speech, a long pause, background noise, a long sentence and a
  reply interrupted by loss of network. Reconnect and confirm a later turn works.
- [ ] Test speaker volume up/down, an exact level, mute/unmute and volume status
  with the configured mixer. Check that an unavailable mixer is reported clearly.
- [ ] Open and close the touch menu, read the command list and choose a personality.
  Check face selection persistence, expression changes, idle screen and wake-up.
- [ ] Try companion mode on/off, an offered memory follow-up and “Don't bring that
  up again”. Confirm privacy controls still take precedence.

## Phone browser and iPhone shortcut

- [ ] Open the configured HTTPS address on the phone, enter its connection key,
  and try a text exchange. Check the web command and personality menus.
- [ ] Allow microphone access, speak and listen to a reply. Deny access once and
  confirm the page gives a useful error. Check the certificate and secure context.
- [ ] Change personality while a reply is pending; the older reply must not undo
  the newer selection. Navigate away/back and confirm old audio or an old request
  does not come back to life. Retry after locking/unlocking the phone.
- [ ] Run the configured iPhone shortcut, including dictation, the authenticated
  request and reading its answer. Check a request with a wrong key fails cleanly.
- [ ] If remote access is part of the setup, repeat over mobile data using the
  configured private network or HTTPS route. Record the route type, not credentials.

## Home automation and optional notifications

- [ ] Ask which devices Iris can control and read the test device's state.
- [ ] Switch only the selected harmless device on/off and verify its physical state.
  Check metering only if that device supports it.
- [ ] On a disposable schedule, read, edit and execute a near-future event. Check
  timezone and clock synchronization, then remove the test schedule and restore
  the device's original state. Confirm normal schedules remain intact.
- [ ] Make the test device briefly unavailable. Verify schedule/device errors are
  visible, other reads still work and recovery does not duplicate an action you
  cannot safely repeat. Do not use a heater, lock or safety-related device for this.
- [ ] If configured, explicitly ask for one synthetic Pushover notification and
  verify receipt on the phone. A successful API response alone is not delivery proof.

## Updater and recovery acceptance

Run fault experiments in a disposable checkout and environment, with synthetic
archives and dedicated test service names. Keep the production updater stopped
while preparing those experiments. The [update guide](updates.md) explains the
saved stages and manual recovery procedure.

- [ ] Windows: set the token in the watcher environment, stop the manually started
  server, then run the watcher. Confirm authenticated readiness and a matching
  instance identity; try Ctrl+C and confirm the listener and child processes stop.
- [ ] Apply a controlled fast-forward on a test branch. Confirm the previous process
  exits before dependency installation, the install runs once, and `pending.json`
  disappears only after the replacement stays ready.
- [ ] Make a test install fail, then fix it and retry. Repeat with a launcher that
  exits after startup, a wrong health token and an unavailable readiness endpoint.
  Each failure must retain pending state and must not be reported as completion.
- [ ] Simulate a stopped watcher between installation and readiness. Restart it
  and confirm completion resumes without repeating a successful install hook.
- [ ] Move HEAD manually while an update is pending. Confirm the updater stops
  for review, then follow the documented manual reconciliation using that test checkout.
- [ ] Pi: test a running client plus home service, an intentionally inactive unit,
  a missing optional unit and a unit that fails on startup. Confirm inactive/missing
  units stay stopped, originally active/failed units are restored, and restart loops
  do not pass the stability check. Reboot and verify the timer remains configured.
- [ ] Test network loss, tracked edits, divergent history and an ignored-file
  collision in the disposable checkout. Preserve local files and inspect any
  pending state before resuming. The updater does not merge conflicts for you.
- [ ] Restore a synthetic archive backup with Iris stopped and confirm source text,
  privacy state and recall remain correct after restart. Restore matching software
  and backup together when an old release cannot read the newer schema.

## Keep the evidence useful

Record the commit, commands and results, and describe any missing model or hardware
checks explicitly. Share redacted errors or safe timing logs rather than a complete
raw trace. The [diagnostics guide](performance-and-recovery.md) explains what those
logs contain and why raw tracing needs separate care.

A release is ready when the required automated checks and applicable device checks
have passed. Any unresolved item needs an owner and a clear limitation; a checkbox
should never imply that a simulated device was the real one.
