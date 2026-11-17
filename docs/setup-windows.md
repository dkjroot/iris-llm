# The complete Windows 11 route

This route targets **Windows 11 on a 64-bit x86 PC**, using KoboldCPP for local chat,
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

Every command below runs on the **host in PowerShell** until a step explicitly
sends you to the Pi or iPhone. A command's working directory matters: it tells
Iris where relative configuration paths start. Text in CAPITAL_PLACEHOLDERS must
be replaced; don't include angle brackets around your replacement.

Open Start, type **PowerShell**, and open it normally. Most commands here do not
need Administrator. Install Git and uv (a tool that installs Python and its packages):

```powershell
winget install --id Git.Git -e
winget install --id astral-sh.uv -e
```

Close and reopen PowerShell so it sees the new commands. Run `git --version` and
`uv --version`; each should print a version. If winget is unavailable, use
[Git's installer](https://git-scm.com/download/win) and
[uv's Windows instructions](https://docs.astral.sh/uv/getting-started/installation/).
Do not run the later package-install commands as Administrator.

## 2. Get one local chat model answering

In a browser, open [KoboldCPP releases](https://github.com/LostRuins/koboldcpp/releases).
Download the Windows executable for your hardware. Make `iris-models` inside your
user folder in File Explorer and move the executable there, naming it
`koboldcpp.exe` for the examples below. Double-click it to open the launcher.
The [project's download notes](https://github.com/LostRuins/koboldcpp) distinguish
GPU and older-hardware builds. Check the exact GPU in Task Manager; “24 GB” alone
doesn't identify a compatible driver/backend.

Follow [Prepare the local chat model](local-models.md) now. It explains the GGUF
download, context allocation, localhost listener and first reply. Save the working
`iris-model.kcpps` file. Then come back here to step 3. Leave KoboldCPP running.

## 3. Install Iris

```powershell
Set-Location $HOME
git clone https://github.com/dkjroot/iris-llm.git
Set-Location iris-llm
uv sync --locked --python 3.12 --extra whisper
.\.venv\Scripts\python.exe --version
```

The last command should show Python 3.12. `.venv` is Iris's private Python
installation. We use its full path so there is no activation-policy puzzle.
For a new terminal, return with `Set-Location "$HOME\iris-llm"`.

If the checkout already exists, don't clone over it; use its directory and first
check `git status --short --branch`. The locked sync installs declared packages,
not speech weights. The separate Pocket environment below is intentional.

## 4. Prepare the voice and configure Iris

Make `reference.wav` with [the short voice-recording steps](voice-reference.md),
then return here. The file belongs in your home folder's `iris-models` directory.
If you prefer supplied Kokoro voices, use the alternative at the end of that
chapter and keep `--extra kokoro` in later sync commands.

From the Iris checkout, run:

```powershell
uv venv --python 3.11 .venv-pocket
uv pip install --python .venv-pocket\Scripts\python.exe --index https://download.pytorch.org/whl/cpu "torch==2.8.0+cpu"
uv pip install --python .venv-pocket\Scripts\python.exe -r iris_tts_engines/pocket-requirements.txt
.\.venv\Scripts\python.exe iris.py voice --engine pocket download
.\.venv\Scripts\python.exe iris.py voice --engine pocket prepare "$HOME\iris-models\reference.wav" my-iris-voice
.\.venv\Scripts\python.exe iris.py configure
```

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

```powershell
.\.venv\Scripts\python.exe iris.py --check
.\.venv\Scripts\python.exe iris.py --download-models
```

The first command should list the model ID. If needed, rerun configuration with
that exact ID. The second prepares Whisper and the selected synthesiser while
you are online. Missing files at startup are a setup error; Iris does not silently
fetch them in the middle of a conversation. If Pocket preparation failed, read
its error before starting the server; there is no automatic default voice fallback.

## 5. Make the connection key and try text locally

The connection key lets clients use your Iris server. Generate it once, then use
the same value on each chosen client. It is separate from a model provider key.

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$env:IRIS_DROID_TOKEN = 'PASTE_THE_GENERATED_KEY'
```

Keep the generated value in your password manager. Replace the quoted placeholder
with that value, retaining the quotes. To let a fresh Windows login/startup task
inherit it, save it for your user account as well:

```powershell
[Environment]::SetEnvironmentVariable('IRIS_DROID_TOKEN', $env:IRIS_DROID_TOKEN, 'User')
```

This is a local user environment value, not an encrypted vault. Don't commit it.
Already-running programs do not acquire the new value automatically.

Start Iris in this terminal:

```powershell
.\.venv\Scripts\python.exe iris.py server --web-chat
```

Wait for the server to finish loading. In a second host terminal, change to the
Iris checkout, set/load the same key, and run:

```powershell
.\.venv\Scripts\python.exe iris.py client --text
```

Type a short greeting. You should see a written answer. `401` means the keys do
not match; model errors mean check the still-running KoboldCPP process and model
ID. A text client needs no microphone, but the current server still loads its
configured speech engines. You now have a useful minimal installation.

## 6. Let the Pi and phone reach the host

In PowerShell run `ipconfig`. Under the connected Wi-Fi/Ethernet adapter, find
its IPv4 address, not a VPN/virtual adapter's address. Write it down as **HOST_IP**.
Reserve that address for this PC in the router's DHCP settings if possible.
Set the trusted home connection to **Private** in Windows network settings.

Stop only the Iris server with Ctrl+C and restart it from the keyed host terminal:

```powershell
.\.venv\Scripts\python.exe iris.py server --host 0.0.0.0 --web-chat
```

Open a separate **Administrator PowerShell** just for the firewall rule:

```powershell
New-NetFirewallRule -DisplayName 'Iris home LAN' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765,8443 -Profile Private -RemoteAddress LocalSubnet
```

This allows the private local subnet, not every public network. Close the admin
window afterwards. [Microsoft documents these rule fields](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule).
Do not add a router port-forward.

`0.0.0.0` is the listening instruction. Clients use HOST_IP, for example
`http://192.168.1.10:8765`, never `http://0.0.0.0`. Keep KoboldCPP on localhost.
A phone on a guest Wi-Fi network may be deliberately unable to reach other devices.

## 7. Add HTTPS for the phone

Skip this step if you don't want a phone browser/shortcut yet. The Pi voice client
can use the trusted-LAN HTTP connection. For phone microphone use, prepare Caddy:

Download Caddy for Windows amd64 from [Caddy's download page](https://caddyserver.com/download).
Make a folder `$HOME\iris-proxy` and put the executable there as `caddy.exe`.
In Notepad, save a file named **Caddyfile** in that folder, using “All files” so
Notepad doesn't append `.txt`. Use the contents below.

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

```powershell
Set-Location "$HOME\iris-proxy"
.\caddy.exe validate --config .\Caddyfile --adapter caddyfile
.\caddy.exe run --config .\Caddyfile --adapter caddyfile
```

Validation must succeed before you leave Caddy running. It creates a local
certificate authority; the phone must trust its public root certificate.
[Caddy's local HTTPS documentation](https://caddyserver.com/docs/automatic-https)
explains that trust relationship. A local-trust-install warning on the host does
not necessarily mean the listener failed; check the listener and phone trust separately.

In another PowerShell window run
`& "$HOME\iris-proxy\caddy.exe" environ` and find `caddy.AppDataDir`.
Inside that directory, find `pki\authorities\local\root.crt`. Keep Caddy under
the same Windows account when setting up startup; another account has another
certificate store. This route runs Caddy natively on Windows, avoiding a second
WSL network layer.

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

On the host, save this as `run_server.ps1` in the Iris checkout. In Notepad use
“All files” and the exact `.ps1` extension. It reads the user environment key you
saved in step 5; it does not install packages on every boot. If you already have
this launcher, keep a copy and merge the relevant settings rather than replacing
your working customisation blindly.

```powershell
Set-Location $PSScriptRoot
foreach ($name in @('IRIS_DROID_TOKEN', 'IRIS_HOME_SERVICE_TOKEN', 'IRIS_PUSHOVER_API_TOKEN', 'IRIS_PUSHOVER_USER_KEY')) {
    $value = [Environment]::GetEnvironmentVariable($name, 'User')
    if ($value) { [Environment]::SetEnvironmentVariable($name, $value, 'Process') }
}
if (-not $env:IRIS_DROID_TOKEN) { throw 'Set the Iris user connection key first.' }
$env:PYTHONUNBUFFERED = '1'
$irisLogDirectory = Join-Path $env:LOCALAPPDATA 'Iris\logs'
New-Item -ItemType Directory -Force $irisLogDirectory | Out-Null
& "$PSScriptRoot\.venv\Scripts\python.exe" iris.py server --host 0.0.0.0 --web-chat *> "$irisLogDirectory\server.log"
exit $LASTEXITCODE
```

If you enabled home/notification credentials, save those as user variables too,
or load them explicitly through your chosen local secret setup before starting
Python. A task does not inherit changes from an old interactive shell.

Open **Task Scheduler → Create Task**. Use your normal account and **Run only when
user is logged on** for this first route; it starts after reboot and login, not
before login. Add a trigger **At log on**, delayed by one minute. Add an action:
program `powershell.exe`, arguments `-NoProfile -File "C:\Users\YOUR_NAME\iris-llm\run_server.ps1"`,
and Start in `C:\Users\YOUR_NAME\iris-llm`. Replace YOUR_NAME with your actual
user-folder name. If local script policy blocks it, set the current user's policy
to `RemoteSigned` only if permitted on your computer; do not bypass an organisation's
policy. The command is `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`;
[Microsoft explains its scope](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/set-executionpolicy).
Test the script manually before scheduling it. Its output goes to
`%LOCALAPPDATA%\Iris\logs\server.log`, replaced at each start. Keep a private copy
before restarting when investigating a failure.

Create separate logon tasks for KoboldCPP and Caddy. Kobold's program is the full
path to `koboldcpp.exe`, arguments `--config "C:\Users\YOUR_NAME\iris-models\iris-model.kcpps"`.
Caddy's program is the full path to `caddy.exe`, arguments
`run --config "C:\Users\YOUR_NAME\iris-proxy\Caddyfile" --adapter caddyfile`.
Use the same user account as the manual test. Set no short runtime limit and
configure a delayed restart on failure. Save task history/errors for troubleshooting.
Give each task its executable's folder in **Start in**, without surrounding
quotes in that field. If phone access was skipped, omit the Caddy task entirely.
For a model/proxy error, stop its task and run that same command in PowerShell
to see the program's output; Task Scheduler's success status alone only means
it launched a process.
An always-on installation before login needs a separately tested service/account
arrangement; this guide does not claim the logon tasks do that.

Stop the manually running copies with Ctrl+C before running the tasks, or the
port will already be occupied. Sign out/in or reboot, log in, then test the Pi,
phone HTTPS and shortcut again. The PC must stay awake for conversations; configure
its power settings deliberately. The Pi's home scheduler can continue while it sleeps.

For auto-updates, replace the Iris task's action with the watcher after testing
[the updater setup](updates.md). Its parent environment needs the same connection
key. Keep the launcher foreground and don't schedule both watcher and manual server.

## 10. Back up, update and prove the restart

Stop the Iris process/service/task before archive maintenance. Run
`.\.venv\Scripts\python.exe iris.py memory inspect` from the checkout to locate/check an enabled
archive, then follow [the stopped backup procedure](memory-storage.md). Keep the
archive, local configuration, `iris-model.kcpps`, prepared voices and secret setup
in a private backup. Home pairing/schedules need their own Pi backup from section J.
A Git checkout alone is not a backup of the working system.

With processes and updater stopped, update the checkout:

```powershell
Set-Location "$HOME\iris-llm"
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
and repeat. Use Task Scheduler history and the foreground process output. For persistent logs, redirect each launcher to a private file and review it for secrets before sharing.

Use [the release checklist](releasing.md) to record actual versions/results,
including a restore with synthetic data. It is fine to leave an optional feature
out; it is not fine to mark an unrun hardware or recovery check as passed.
