# The complete macOS route

This route targets **macOS 14 or newer on an Apple Silicon Mac**, using KoboldCPP for local chat,
faster-whisper on CPU for recognition and Pocket TTS on CPU for the voice.
It then covers the Pi, iPhone, Matter devices and startup. You can stop after a
working text or voice conversation. [The feature map](start-here.md) labels the
optional additions; you do not need home automation to use Iris.

**Validation status:** instructions checked against Iris and upstream documentation;
a fresh full installation on this host platform/Pi/iPhone combination is still
pending. The [reference setup](reference-setup.md) distinguishes reported hardware
from verified results. CPU-only operation can be slow. Model fit and GPU support
need checking on your actual machine.

## 1. Get the equipment and tools ready

For the host, have enough RAM/disk for the model you choose, internet for setup,
and a microphone for preparing a Pocket voice (or an existing clean WAV). A small
local model needs far less hardware than the reference 26B model. Read the memory
planning notes in [local models](local-models.md#pick-a-file-your-machine-can-run).

The later Pi route needs a Pi 4B or another supported 64-bit Pi, its correct power
supply, a 32 GB or larger microSD/SSD, a way to write that storage, USB audio and
the same home LAN. The seven-inch HDMI/USB touch display, Wi-Fi Matter plug and
iPhone are only needed for those chapters. Model downloads and optional vendor
accounts are explained where they are used. No paid chat API is part of this route.

Every command below runs on the **host in Terminal** until a step explicitly
sends you to the Pi or iPhone. A command's working directory matters: it tells
Iris where relative configuration paths start. Text in CAPITAL_PLACEHOLDERS must
be replaced; don't include angle brackets around your replacement.

Open **Applications → Utilities → Terminal**. Run `git --version`; if macOS
offers the Command Line Tools, install them and retry. Install uv using
[its official installer](https://docs.astral.sh/uv/getting-started/installation/):

```sh
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/iris-uv-install.sh
sh /tmp/iris-uv-install.sh
```

Open a new Terminal window and check `uv --version`. These commands install a
user tool; later Iris package commands do not need `sudo`.

## 2. Get one local chat model answering

Download `koboldcpp-mac-arm64` from [KoboldCPP releases](https://github.com/LostRuins/koboldcpp/releases).
In Finder make `iris-models` in your home folder and move the download there.
In Terminal run:

```sh
chmod +x "$HOME/iris-models/koboldcpp-mac-arm64"
"$HOME/iris-models/koboldcpp-mac-arm64"
```

If macOS blocks the verified download, use System Settings → Privacy & Security
to allow that application. This route is for Apple Silicon; Intel Macs require a
different runtime/build and their own acceptance. See
[KoboldCPP's platform notes](https://github.com/LostRuins/koboldcpp).

Follow [Prepare the local chat model](local-models.md) now. It explains the GGUF
download, context allocation, localhost listener and first reply. Save the working
`iris-model.kcpps` file. Then come back here to step 3. Leave KoboldCPP running.

## 3. Install Iris

```sh
cd "$HOME"
git clone https://github.com/dkjroot/iris-llm.git
cd iris-llm
uv sync --locked --python 3.12 --extra whisper
.venv/bin/python --version
```

The last command should show Python 3.12. New Terminal windows start outside the
checkout, so return with `cd "$HOME/iris-llm"`. Using `.venv/bin/python` explicitly
avoids depending on whichever Python macOS finds first.

If the checkout already exists, don't clone over it; use its directory and first
check `git status --short --branch`. The locked sync installs declared packages,
not speech weights. The separate Pocket environment below is intentional.

## 4. Prepare the voice and configure Iris

Make `reference.wav` with [the short voice-recording steps](voice-reference.md),
then return here. The file belongs in your home folder's `iris-models` directory.
If you prefer supplied Kokoro voices, use the alternative at the end of that
chapter and keep `--extra kokoro` in later sync commands.

From the Iris checkout, run:

```sh
uv venv --python 3.11 .venv-pocket
uv pip install --python .venv-pocket/bin/python 'torch==2.8.0'
uv pip install --python .venv-pocket/bin/python -r iris_tts_engines/pocket-requirements.txt
.venv/bin/python iris.py voice --engine pocket download
.venv/bin/python iris.py voice --engine pocket prepare "$HOME/iris-models/reference.wav" my-iris-voice
.venv/bin/python iris.py configure
```

The macOS PyTorch package uses the ordinary package index, not the Linux/Windows
`+cpu` wheel URL. Iris's Pocket worker uses CPU. Metal acceleration in KoboldCPP
is a separate choice; it doesn't make Whisper or Pocket use the Apple GPU.

In the configuration wizard, choose these starting values. Press Enter to keep
other defaults; optional features can wait.

| Question | Answer |
| --- | --- |
| Connection | custom/local compatible endpoint |
| Base URL | `http://127.0.0.1:5001/v1` |
| Model | The identifier advertised by KoboldCPP; correct it after the check below if necessary. |
| Context size | `8192`, matching the model host allocation |
| Recogniser | `whisper`; default `base.en`, CPU, int8 |
| Synthesiser | `pocket` |
| Pocket voice | `file:my-iris-voice` |
| Outbound remote conversation | no |
| Memory/weather/notifications | no initially; enable each deliberately later |

```sh
.venv/bin/python iris.py --check
.venv/bin/python iris.py --download-models
```

The first command should list the model ID. If needed, rerun configuration with
that exact ID. The second prepares Whisper and the selected synthesiser while
you are online. Missing files at startup are a setup error; Iris does not silently
fetch them in the middle of a conversation. If Pocket preparation failed, read
its error before starting the server; there is no automatic default voice fallback.

## 5. Make the connection key and try text locally

The connection key lets clients use your Iris server. Generate it once, then use
the same value on each chosen client. It is separate from a model provider key.

```sh
.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
export IRIS_DROID_TOKEN='PASTE_THE_GENERATED_KEY'
```

Keep the value in your password manager. Save a local shell file
`~/.config/iris/server.env` containing `export IRIS_DROID_TOKEN='your actual key'`;
create its folder with `mkdir -p ~/.config/iris`, edit it with
`nano ~/.config/iris/server.env`, then run `chmod 600 ~/.config/iris/server.env`.
Ctrl+O, Enter saves in nano; Ctrl+X exits. Load it in later shells with
`. "$HOME/.config/iris/server.env"`. It is outside the checkout.

Start Iris in this terminal:

```sh
.venv/bin/python iris.py server --web-chat
```

Wait for the server to finish loading. In a second host terminal, change to the
Iris checkout, set/load the same key, and run:

```sh
.venv/bin/python iris.py client --text
```

Type a short greeting. You should see a written answer. `401` means the keys do
not match; model errors mean check the still-running KoboldCPP process and model
ID. A text client needs no microphone, but the current server still loads its
configured speech engines. You now have a useful minimal installation.

## 6. Let the Pi and phone reach the host

Open System Settings → Network → your connected adapter → Details → TCP/IP and
record the IPv4 address as **HOST_IP**. Reserve it in the router's DHCP settings
if possible. This is the Mac's network address, not `127.0.0.1`.

Stop only the Iris server with Ctrl+C and restart it from the keyed host terminal:

```sh
.venv/bin/python iris.py server --host 0.0.0.0 --web-chat
```

If the macOS firewall asks, allow the Iris Python listener and Caddy on your
trusted home network. System Settings → Network → Firewall → Options controls
application permissions. This application firewall is not a per-port LAN-subnet
rule; keep the host on a trusted network and restrict reachability with your
network firewall if needed. Do not create a router port-forward.

`0.0.0.0` is the listening instruction. Clients use HOST_IP, for example
`http://192.168.1.10:8765`, never `http://0.0.0.0`. Keep KoboldCPP on localhost.
A phone on a guest Wi-Fi network may be deliberately unable to reach other devices.

## 7. Add HTTPS for the phone

Skip this step if you don't want a phone browser/shortcut yet. The Pi voice client
can use the trusted-LAN HTTP connection. For phone microphone use, prepare Caddy:

Download the macOS arm64 binary from [Caddy's download page](https://caddyserver.com/download).
Make `~/iris-proxy`, move/rename the binary to `~/iris-proxy/caddy`, then run
`chmod +x "$HOME/iris-proxy/caddy"`. In Terminal run
`nano "$HOME/iris-proxy/Caddyfile"` and save the following contents.

Replace both HOST_IP values with the host's real, stable IPv4 address:

```caddyfile
{
    auto_https disable_redirects
}
https://HOST_IP:8443 {
    bind HOST_IP
    tls internal
    reverse_proxy 127.0.0.1:8765
}
```

```sh
cd "$HOME/iris-proxy"
./caddy validate --config Caddyfile --adapter caddyfile
./caddy run --config Caddyfile --adapter caddyfile
```

Validation must succeed before you leave Caddy running. It creates a local
certificate authority; the phone must trust its public root certificate.
[Caddy's local HTTPS documentation](https://caddyserver.com/docs/automatic-https)
explains that trust relationship. A local-trust-install warning on the host does
not necessarily mean the listener failed; check the listener and phone trust separately.

Run `"$HOME/iris-proxy/caddy" environ` in another Terminal and find
`caddy.AppDataDir`. Under that directory find `pki/authorities/local/root.crt`.
Use the same logged-in account for manual and automatic Caddy so the certificate
authority does not change. AirDrop can transfer this public certificate to your
iPhone; never transfer `root.key`.

Only transfer `root.crt`, never private-key files. The next chapter walks through
installing and fully trusting it on the iPhone. Don't add a router port-forward
for 8443. This is a home-LAN route, not a remote-public-service setup.

## 8. Add the Pi, phone and chosen extras

Keep the host's model, Iris and Caddy processes running. Follow
[Pi, phone and home setup, sections A–J](setup-devices.md) now. Each section names
its machine, expected result and optional status. It covers microphone/playback,
wake/cancel, display/touch, phone HTTPS, Siri, Matter commissioning, schedules,
memory, personalities, companion mode and notifications.

For the complete reference setup, finish every applicable section. For a smaller
setup, skip an optional section explicitly; a missing smart plug is not a failed
voice installation. At the end return here to step 9 so startup and recovery are
part of this route too.

## 9. Keep the host running after closing terminals

Create `~/iris-models/start-iris.sh` with these contents and make it executable
with `chmod +x "$HOME/iris-models/start-iris.sh"`:

```sh
#!/bin/sh
set -eu
. "$HOME/.config/iris/server.env"
cd "$HOME/iris-llm"
exec "$HOME/iris-llm/.venv/bin/python" iris.py server --host 0.0.0.0 --web-chat
```

Use `launchd`, macOS's process manager, to run it at login. Create
`~/Library/LaunchAgents/local.iris.server.plist` (first run
`mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"`, then open the file
with `nano "$HOME/Library/LaunchAgents/local.iris.server.plist"`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.iris.server</string>
  <key>ProgramArguments</key><array><string>/Users/YOUR_NAME/iris-models/start-iris.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>20</integer>
  <key>StandardOutPath</key><string>/Users/YOUR_NAME/Library/Logs/iris-server.log</string>
  <key>StandardErrorPath</key><string>/Users/YOUR_NAME/Library/Logs/iris-server-error.log</string>
</dict></plist>
```

Replace every YOUR_NAME with the actual user-folder name; launchd does not expand
`~` inside these XML strings. Validate with `plutil -lint PATH_TO_THE_PLIST`.
Make two copies of the server plist, named `local.iris.model.plist` and
`local.iris.proxy.plist` in the same folder. In each copy change the Label string
to match its filename without `.plist`, and change both log filenames to
`iris-model` or `iris-proxy` instead of `iris-server`. Replace the whole
ProgramArguments array with the appropriate one below:

```xml
<array>
  <string>/Users/YOUR_NAME/iris-models/koboldcpp-mac-arm64</string>
  <string>--config</string>
  <string>/Users/YOUR_NAME/iris-models/iris-model.kcpps</string>
</array>
```

```xml
<array>
  <string>/Users/YOUR_NAME/iris-proxy/caddy</string>
  <string>run</string>
  <string>--config</string>
  <string>/Users/YOUR_NAME/iris-proxy/Caddyfile</string>
  <string>--adapter</string>
  <string>caddyfile</string>
</array>
```

Replace YOUR_NAME in these copies too. Run `plutil -lint` on each finished file.
If phone access was skipped, omit the proxy file and its loading steps.
[Apple's launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html)
explains the job keys and log destinations.

Stop the three manual processes first. Load each with
`launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.iris.server.plist"`,
substituting its filename for model and proxy. Inspect with
`launchctl print "gui/$(id -u)/local.iris.server"`. To stop for maintenance use
`launchctl bootout "gui/$(id -u)" PATH_TO_THE_PLIST`; KeepAlive otherwise restarts it.
This is a login-agent setup, not a service available before anyone logs in.
Reboot, log in and test all chosen clients again. Allow the Mac to remain awake
when you want conversations; laptop lid/sleep settings still apply.

Use manual Git updates on this route. The supplied Windows watcher and systemd
service manager are not macOS launchd integrations.

## 10. Back up, update and prove the restart

Stop the Iris process/service/task before archive maintenance. Run
`.venv/bin/python iris.py memory inspect` from the checkout to locate/check an enabled
archive, then follow [the stopped backup procedure](memory-storage.md). Keep the
archive, local configuration, `iris-model.kcpps`, prepared voices and secret setup
in a private backup. Home pairing/schedules need their own Pi backup from section J.
A Git checkout alone is not a backup of the working system.

With processes and updater stopped, update the checkout:

```sh
cd "$HOME/iris-llm"
git status --short --branch
git pull --ff-only
uv sync --locked --python 3.12 --extra whisper
```

If you selected additional extras, include all of them in the sync command; a
sync can remove packages outside its selected profile. Pocket's environment is
separate: update it only with the reviewed worker requirements for that release.
If Git reports edits/divergence, stop and preserve them rather than forcing a reset.
For pending updater state or schema rollback, use [the recovery guide](updates.md).

Restart the model, Iris and proxy, then test one typed and one spoken exchange,
phone HTTPS, the shortcut and the harmless home device. Reboot both host and Pi
and repeat. Read the private files in ~/Library/Logs named in the plists, and use launchctl print for process state.

Use [the release checklist](releasing.md) to record actual versions/results,
including a restore with synthetic data. It is fine to leave an optional feature
out; it is not fine to mark an unrun hardware or recovery check as passed.
