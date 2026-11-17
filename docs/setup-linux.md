# The complete Ubuntu 24.04 LTS route

This route targets **Ubuntu 24.04 LTS on a 64-bit x86 host**, using KoboldCPP for local chat,
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

Open Terminal with Ctrl+Alt+T. Install the basic OS packages, then uv:

```sh
sudo apt update
sudo apt install git curl ca-certificates libportaudio2
curl -LsSf https://astral.sh/uv/install.sh -o /tmp/iris-uv-install.sh
sh /tmp/iris-uv-install.sh
```

`sudo` asks for your account password and runs that command as administrator.
The uv installer and later Python package commands run as your normal user.
Open a new terminal and check `git --version` and `uv --version`.
The installer is documented by [uv](https://docs.astral.sh/uv/getting-started/installation/).

## 2. Get one local chat model answering

Download the matching x86-64 binary from [KoboldCPP releases](https://github.com/LostRuins/koboldcpp/releases).
Make `~/iris-models` and put it there. The examples use `koboldcpp-linux-x64`:

```sh
chmod +x "$HOME/iris-models/koboldcpp-linux-x64"
"$HOME/iris-models/koboldcpp-linux-x64"
```

Choose the no-CUDA build if appropriate and substitute its actual filename in
later service commands. [KoboldCPP's platform notes](https://github.com/LostRuins/koboldcpp)
explain the builds. This Ubuntu/x86 route does not establish support for every
Linux distribution, ARM host, graphics card or driver.

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

Expect Python 3.12. New terminals need `cd "$HOME/iris-llm"` again.
Use `.venv/bin/python` for Iris; don't install its packages into Ubuntu's system
Python or bypass the OS package protection.

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
uv pip install --python .venv-pocket/bin/python --index https://download.pytorch.org/whl/cpu 'torch==2.8.0+cpu'
uv pip install --python .venv-pocket/bin/python -r iris_tts_engines/pocket-requirements.txt
.venv/bin/python iris.py voice --engine pocket download
.venv/bin/python iris.py voice --engine pocket prepare "$HOME/iris-models/reference.wav" my-iris-voice
.venv/bin/python iris.py configure
```

This selects CPU PyTorch for Pocket so speech does not consume the GPU reserved
for the chat model. GPU recognition is another optional change after the CPU
route works; install only the libraries required by your chosen backend.

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

Keep the value in your password manager. Make `~/.config/iris` with
`mkdir -p ~/.config/iris` and run `nano ~/.config/iris/server.env`. Save one line:
`IRIS_DROID_TOKEN=your-generated-key` (no `export` in this systemd environment file).
Ctrl+O, Enter saves; Ctrl+X exits. Protect it with `chmod 600 ~/.config/iris/server.env`.
For later interactive shells use `set -a; . "$HOME/.config/iris/server.env"; set +a`.
Keep other enabled Iris credentials in that file too, one `NAME=value` per line.

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

Run `hostname -I` and `ip route get 1.1.1.1`. The latter shows the selected
network interface and its `src` address without sending a conversation. Record
the host's LAN IPv4 address as **HOST_IP**, avoiding Docker/VPN interfaces.
A router DHCP reservation keeps that address stable.

Stop only the Iris server with Ctrl+C and restart it from the keyed host terminal:

```sh
.venv/bin/python iris.py server --host 0.0.0.0 --web-chat
```

If UFW is enabled (`sudo ufw status`), allow your actual home subnet to the two
ports. Replace `192.168.1.0/24` if your network differs:

```sh
sudo ufw allow from 192.168.1.0/24 to any port 8765 proto tcp
sudo ufw allow from 192.168.1.0/24 to any port 8443 proto tcp
```

Do not enable or reset a firewall casually over SSH; preserve your administration
access. A different firewall needs equivalent local rules. No router forwarding
is needed for devices on the same LAN.

`0.0.0.0` is the listening instruction. Clients use HOST_IP, for example
`http://192.168.1.10:8765`, never `http://0.0.0.0`. Keep KoboldCPP on localhost.
A phone on a guest Wi-Fi network may be deliberately unable to reach other devices.

## 7. Add HTTPS for the phone

Skip this step if you don't want a phone browser/shortcut yet. The Pi voice client
can use the trusted-LAN HTTP connection. For phone microphone use, prepare Caddy:

For this walkthrough, download Caddy's Linux amd64 binary from
[the official download page](https://caddyserver.com/download). Make `~/iris-proxy`,
put it there as `caddy` and run `chmod +x "$HOME/iris-proxy/caddy"`.
Run `nano "$HOME/iris-proxy/Caddyfile"` and save the contents below.
The packaged system service is another supported deployment, but has a different
user/certificate store; don't run both on the same port.

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

In another terminal run `"$HOME/iris-proxy/caddy" environ` and find
`caddy.AppDataDir`. Its `pki/authorities/local/root.crt` is the public root
certificate needed by the iPhone. Use this same user account for the service
below, so Caddy keeps using this authority rather than creating another one.

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

Create `~/.config/systemd/user` with `mkdir -p ~/.config/systemd/user`.
Open `nano ~/.config/systemd/user/iris-server.service` and save this content
(Ctrl+O, Enter, Ctrl+X):

```ini
[Unit]
Description=Iris conversation server
StartLimitIntervalSec=0
[Service]
WorkingDirectory=%h/iris-llm
EnvironmentFile=%h/.config/iris/server.env
Environment=PYTHONUNBUFFERED=1
ExecStart=%h/iris-llm/.venv/bin/python iris.py server --host 0.0.0.0 --web-chat
Restart=on-failure
RestartSec=20
KillSignal=SIGINT
TimeoutStopSec=30
[Install]
WantedBy=default.target
```

Save two more files with the same Unit/Install structure and appropriate descriptions.
For `iris-model.service`, omit EnvironmentFile, use `WorkingDirectory=%h/iris-models`
and `ExecStart=%h/iris-models/koboldcpp-linux-x64 --config %h/iris-models/iris-model.kcpps`.
For `iris-proxy.service`, omit EnvironmentFile, use `WorkingDirectory=%h/iris-proxy`
and `ExecStart=%h/iris-proxy/caddy run --config %h/iris-proxy/Caddyfile --adapter caddyfile`.
`%h` is expanded by systemd to your home directory. If your paths contain spaces,
quote them in the unit according to systemd's syntax.
Use nano to create each file, copying the server unit and making the substitutions
above. If phone access was skipped, omit the proxy file and remove
`iris-proxy.service` from the commands below.

Stop the manually running processes, then run:

```sh
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now iris-model.service iris-server.service iris-proxy.service
systemctl --user status iris-model.service iris-server.service iris-proxy.service
```

Lingering lets these user services run without an open terminal or login session.
Check logs with `journalctl --user -u iris-server.service -n 50`; substitute the
other service names when needed. Reboot and verify the Pi and phone before
calling startup complete. Suspend/sleep still stops the host from answering.
To stop for maintenance, run
`systemctl --user stop iris-server.service iris-model.service iris-proxy.service`,
omitting any service you didn't install. Start them again with `start` in place
of `stop`; use `disable --now` only when you want to remove automatic startup.

Manual updates are below. If you later configure the supplied updater with
`--service iris-server.service`, the installed profile and health probe must match
this server; don't copy the Pi's default managed-service names unchanged.

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
and repeat. Use journalctl --user -u iris-server.service and the corresponding model/proxy units. Keep unredacted logs private.

Use [the release checklist](releasing.md) to record actual versions/results,
including a restore with synthetic data. It is fine to leave an optional feature
out; it is not fine to mark an unrun hardware or recovery check as passed.
