# Add the Pi, phone and the rest of the house

This is the shared second half of the Windows, macOS and Ubuntu walkthroughs.
Your host should already answer a typed Iris message. Keep its model server and
Iris running. Keep Caddy running too if you chose phone access. Work through A–J,
skipping the optional equipment you don't want, then return to your host guide's
step 9. There is no need to buy a Pi, display or smart plug just to try Iris.

`HOST_IP` means the host's reserved LAN address, `PI_IP` the Pi's, and `PI_USER`
the account you create below. Replace those words wherever they occur. Addresses
starting `127.` refer to the computer running the command, not your other computer.
Commands labelled **Pi** run in the SSH window connected to the Pi; they use its
Bash shell regardless of whether your host is Windows, macOS or Linux.

These instructions have been checked against the code. Fresh hardware runs and
a walkthrough by a new user remain [acceptance checks](verification.md). In
particular, the display model, Matter firmware and exact model files affect the
result; the [reference setup](reference-setup.md) records what is known.

## A. Put an operating system on the Pi

**Optional equipment: Pi, power supply and storage. Host: browser and Imager.**
This route uses a Pi 4B and **Raspberry Pi OS Lite, 64-bit, Bookworm**, available
as the legacy image. This is a named route, not a claim that every newer image
has been tested. Current Pi OS also offers Trixie; record the actual OS if you
choose it and check its Python and Matter build dependencies separately.
[Official images](https://www.raspberrypi.com/software/operating-systems/).

1. Connect a spare microSD card or SSD to the host. Writing the image **erases
   that selected storage**, so identify it by size/name and keep your existing
   working Pi card out of the writer.
2. Install and open [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
   Select your Pi, the Lite 64-bit Bookworm image and the spare storage. In OS
   customisation set hostname `iris-pi`, a username/password you will remember,
   your country/timezone, and your Wi-Fi details if not using Ethernet. Enable
   SSH with password authentication for this first connection. Save and write.
3. Eject the storage. Put it in the Pi; connect Ethernet if using it, the USB
   microphone/speaker, and power. For the optional panel, HDMI carries the
   picture and a USB **data** connection carries touch. Use the panel maker's
   power instructions. Let the first boot finish.
4. Open a new host terminal and run `ssh PI_USER@iris-pi.local`. Accept the host
   fingerprint only for the Pi you just set up, then enter its password. If the
   name does not resolve, find the Pi in your router's connected-device list
   and use `ssh PI_USER@PI_IP`. Reserve that address in the router.

You should now see a prompt belonging to the Pi. `cat /etc/os-release` should
identify Bookworm, `uname -m` should say `aarch64`, and `python3 --version` should
be 3.11 on this route. Iris accepts Python 3.11–3.13. The Pi's
[getting-started guide](https://www.raspberrypi.com/documentation/computers/getting-started.html)
explains Imager and first connection if your screen differs. On Windows, install
the **OpenSSH Client** optional feature if `ssh` is unavailable; it is the client,
not an SSH server you need on the PC.

## B. Get a conversation working on the Pi

**Pi: SSH terminal, normal account.** `sudo` is used only for OS packages.

```sh
sudo apt update
sudo apt install git curl unzip python3-venv python3-dev build-essential portaudio19-dev libportaudio2 alsa-utils
cd "$HOME"
git clone https://github.com/dkjroot/iris-llm.git
cd iris-llm
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[microphone,vad]' 'vosk>=0.3.44,<0.4'
export IRIS_DROID_TOKEN='PASTE_THE_HOST_CONNECTION_KEY'
.venv/bin/python iris.py client --server http://HOST_IP:8765 --text
```

Paste the same key as the host; do not generate a second one. This Pi route uses
pip, so there is no uv environment to activate. In a new SSH window, return to
`~/iris-llm` and set the key again until section I installs its service environment.
Type a greeting, wait for the printed reply, then type `exit`. If it fails,
check the host's LAN listener, firewall, address and key before touching audio.

List the devices on the **Pi**:

```sh
.venv/bin/python iris.py client --list-microphones
.venv/bin/python -m sounddevice
```

Pick the microphone name from the first list and an output device from the
second. Their numeric indices are not interchangeable. Names are better for
startup because USB card numbers can change. Try:

```sh
.venv/bin/python iris.py client --server http://HOST_IP:8765 --microphone 'YOUR MICROPHONE NAME' --speaker 'YOUR SPEAKER NAME'
```

Press Enter, wait for the listening cue, speak, then pause. Expect a transcription
and a spoken reply. If the transcription is wrong, check recording; if the text
is right but there is no sound, check the selected speaker and volume. The
[audio diagnostics](client-setup.md#4-set-up-listening-and-audio-devices) let you
record/play locally without involving the model. Ctrl+C stops the client.

Run `.venv/bin/python configure_client.py` and save the working server address,
microphone and speaker in `client_config.json`. Start again with
`.venv/bin/python iris.py client` to prove the saved settings work. The Pi does
not need Whisper, Pocket or the host's chat model downloads.

## C. Add hands-free listening and cancellation

**Optional. Pi: SSH terminal in `~/iris-llm`.** Vosk listens for the wake/cancel
phrases locally. Silero VAD decides whether a sound resembles speech; it is not
speaker identification. Download the small English Vosk model from the
[official model list](https://alphacephei.com/vosk/models):

```sh
mkdir -p .cache/vosk
curl -fL https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip -o .cache/vosk/model.zip
unzip .cache/vosk/model.zip -d .cache/vosk
.venv/bin/python iris.py client --download-vad-model .cache/vad/silero-v6.2.onnx
```

In `configure_client.py`, choose activation `wake`, wake model
`.cache/vosk/vosk-model-small-en-us-0.15`, enable voice cancellation and select
the downloaded VAD model. The [client reference](configuration-reference.md#client-file-and-command-line)
lists the exact keys and flags. Start the client again. Say “hey iris”, wait for
the listening cue, then speak. After her reply, speak again without another wake
phrase. Say “stop listening” to close that conversation window.

Try “cancel, cancel” during the processing sound after an ordinary harmless
question. You should hear cancellation and no answer should be saved for that
turn. Cancellation has an acceptance deadline; it cannot undo an already accepted
device action. Read [the cancellation window](client-setup.md#cancel-something-iris-picked-up-by-mistake)
before relying on it. While a conversation is open, detected speech goes to the
PC. Test your actual accent, room noise and microphone distance.

## D. Put the face on the touchscreen

**Optional. Pi: SSH terminal; HDMI/USB display attached.** First set `face` to
`signal` in the client wizard and restart the client. It now provides a local
display feed. To draw that feed on a Pi OS Lite console:

```sh
sudo apt install python3-pygame
sudo install -m 0644 systemd/iris-face@.service /etc/systemd/system/iris-face@.service
sudo systemctl daemon-reload
sudo systemctl mask --now getty@tty1.service
sudo systemctl enable --now "iris-face@$USER.service"
sudo journalctl -u "iris-face@$USER.service" -b -n 30
```

Keep SSH connected before masking the local login prompt. The unit expects the
checkout at `/home/YOUR_USER/iris-llm`; edit its WorkingDirectory if yours differs.
The screen can be black until the voice client activates the face. Say the wake
phrase, then tap the face to open the menu and the smiley to change heads. Check
all screen edges and touch targets. A seven-inch screen does not imply a specific
resolution: use automatic detection first. [Display setup](face-display.md)
covers panel timing, rotation, the desktop preview and how to restore the login
prompt. Section I will make the voice client start at boot too.

## E. Connect the iPhone browser

**Optional. Host: file manager; iPhone: Settings and Safari.** This is for home
Wi-Fi. Neither this guide nor Caddy's local certificate gives you access from
mobile data or away from home. Leave router port forwarding off.

1. Find the Caddy `root.crt` using step 7 of your host guide. Transfer **only that
   public certificate** to your iPhone, for example by AirDrop from a Mac or as
   an attachment sent through your own account. Keep private keys on the host.
2. Open the certificate on the iPhone. In Settings, open **Profile Downloaded**
   (or **General → VPN & Device Management**), select the Caddy certificate and
   install it using your device passcode.
3. Open **Settings → General → About → Certificate Trust Settings**. Enable
   full trust for that Caddy root. Installing the profile alone is insufficient.
   [Apple's instructions](https://support.apple.com/en-us/102390).
4. In Safari open `https://HOST_IP:8443/chat`. The address must match the one in
   the Caddyfile. There should be no certificate warning. Enter your Iris
   connection key and send a typed greeting.
5. Tap **Microphone**, allow access, speak, and listen for the reply. Tap it again
   to stop listening. Try **Remember this phone** if you want to retain the key
   in that browser; unticking it removes that saved copy. Treat it as a key to
   your Iris installation, including any enabled home tools.

If the page never loads, check Caddy, the host firewall and home Wi-Fi isolation.
If text works but the microphone does not, check HTTPS trust and Safari microphone
permission before changing Whisper. If Caddy later runs under another user, it
may have another root certificate. [Phone troubleshooting](phone-access.md)
also covers the existing WSL variant on Windows.

## F. Build the Siri shortcut

**Optional. iPhone: Shortcuts.** Complete E first. This sends text to Iris and
uses the iPhone's voice to read the reply; it does not use Pocket for playback.

1. On the host, from the Iris checkout, generate one session ID:
   `.venv/bin/python -c "import uuid; print(uuid.uuid4())"` on macOS/Linux, or
   `.\.venv\Scripts\python.exe -c "import uuid; print(uuid.uuid4())"` in PowerShell.
   Keep that ID for this shortcut so follow-up questions share a conversation.
2. On the phone, create a shortcut named **Iris Relay**. Add **Ask for Input**,
   type **Text**, with prompt “What do you want to ask Iris?”
3. Add **Get Contents of URL**, URL `https://HOST_IP:8443/v1/text`, method **POST**.
   Add `Authorization` = `Bearer YOUR_CONNECTION_KEY`, `Content-Type` =
   `text/plain`, and `X-Iris-Session` = the UUID from step 1. Replace the key
   placeholder, keeping the space after `Bearer`.
4. Set **Request Body** to **File**, selecting the **Provided Input** variable
   from Ask for Input. This sends the actual words, rather than a JSON wrapper.
5. Add **Get Dictionary Value**, key `text`, input **Contents of URL**. Add
   **Speak Text**, input **Dictionary Value**.
6. Run ▶ in Shortcuts and ask a harmless question. Allow the connection if asked.
   Then say “Hey Siri, Iris Relay” and answer its prompt. Both routes should
   produce a spoken Iris answer.

The [phone guide](phone-access.md#talk-to-iris-with-siri) has the same action list
and Apple's supporting references. If it goes silent, temporarily put **Quick
Look** after Get Contents of URL to inspect the response. A 401 usually means a
key/header typo. Remove that diagnostic action after testing. Don't share a
shortcut containing your real key.

## G. Add a Matter over Wi-Fi plug

**Optional. Pi: controller and home service. Host: Iris configuration.** This
route controls Matter switches and supported meters. It is not a general bridge
for every product marketed as “smart”. Start with a lamp you can safely turn off,
on the same ordinary LAN as the Pi. No Thread border router is needed for this
Wi-Fi route. Some vendor apps require an account for initial setup/firmware.

1. Follow [Prepare the Matter controller](matter-controller.md), steps 1–4, on
   the Pi. This includes building `chip-tool`, pairing, finding the actual node
   and endpoints, and proving physical control/read-back. Return here afterwards.
   Building the controller is the most involved optional part; do not make it
   a prerequisite for enjoying a working voice client.
2. On the Pi, `hostname -I` shows its addresses. Use the reserved IPv4 address as
   `PI_IP`. Run `install -d -m 700 ~/.config/iris ~/.local/state/iris-home`.
   Open `nano ~/.config/iris/home.json`. Copy the explained JSON in
   [home service setup](home-automation.md#set-up-the-home-service), replacing
   `192.168.1.40` with PI_IP and `/home/pi` with your actual home directory
   (`echo "$HOME"`). Use your node/switch endpoint. **Omit** power/energy
   endpoints if the device does not expose those clusters. Save with Ctrl+O,
   Enter, then Ctrl+X. Name the device something easy to say, such as
   `living room lamp`. Keep the database and pairing paths as in that guide.
3. Still on the Pi, generate its TLS certificate and service key. Replace PI_IP
   below. This is a separate key and certificate from the PC's Caddy setup:

```sh
umask 077
cd "$HOME/.config/iris"
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 -subj '/CN=Iris home service' -addext 'subjectAltName=IP:PI_IP' -keyout home.key -out home.crt
printf 'IRIS_HOME_TOKEN=%s\n' "$(openssl rand -hex 32)" > home.env
cd "$HOME/iris-llm"
mkdir -p "$HOME/.config/systemd/user"
cp systemd/iris-home.service "$HOME/.config/systemd/user/"
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now iris-home.service
journalctl --user -u iris-home.service -n 30
```

4. On the **host**, copy the public certificate using
   `scp PI_USER@PI_IP:.config/iris/home.crt .` from the Iris checkout. It creates
   `home.crt` there; keep the private `home.key` on the Pi. In the host's
   `local-config.json`, add `home_service_url` = `https://PI_IP:8766` and
   `home_service_ca` = the absolute path to this copied certificate. JSON paths
   on Windows can use forward slashes, for example `C:/Users/Dave/iris-llm/home.crt`.
   Preserve the other settings and valid JSON commas; don't replace the whole
   file with a two-field example. Leave `tools_enabled` true.
5. Read `home.env` privately on the Pi and copy the value after `=` into the
   **host's** `IRIS_HOME_SERVICE_TOKEN`. On Windows set it in the current
   PowerShell environment and save it as a User variable as for the connection
   key. On macOS add `export IRIS_HOME_SERVICE_TOKEN='VALUE'` to the private
   `~/.config/iris/server.env`; on Ubuntu add `IRIS_HOME_SERVICE_TOKEN=VALUE`.
   Reload that file in your host terminal as in step 5 of its guide, and restart
   Iris. Never paste this token into an issue or a committed configuration file.
6. Ask Iris to list home devices, switch the lamp on, and then ask whether it is
   on. Look at the physical lamp too. Try a supported meter if configured.
   Schedule a change a few minutes ahead, ask to read the schedule back, wait
   for the physical change, then remove the test schedule and read it back again.
   A reassuring sentence alone is not proof that the tool or device worked.

The home journal should remain healthy. A TLS error usually means the IP or
certificate path differs; an authentication error means the two home keys don't
match. The scheduler needs the right timezone and synchronized clock. Check
`timedatectl status` and the configured clock-sync file. Device names, revisions,
DST handling and recovery are explained in [home automation](home-automation.md).
Keep existing vendor schedules from fighting Iris's schedule. Certificates
expire: renew the home certificate before its one-year expiry and update the
host's trusted copy. Stop the home service before manual `chip-tool` commands.

## H. Choose the other optional features

**Host configuration and either client.** Enable one addition, restart the
affected process, and check it before continuing.

| Addition | How to enable it | What to check |
| --- | --- | --- |
| Conversation memory | Run the host's configuration wizard again; enable memory and keep a stable owner/database. | Say “memory status”, tell Iris a distinctive harmless fact, restart, then ask for it. See [memory and privacy](memory-and-privacy.md). |
| Private conversation | Say “private mode on”, then “memory status”; finish with “private mode off”. | The private indicator appears, and private conversation is not added to saved history. Privacy is shared across clients for that owner. |
| Semantic recall | Install the host `memory` extra along with its existing `whisper` extra, then use the preparation steps in [semantic recall](memory-and-privacy.md#optional-semantic-recall). | Inspect/index using the documented memory commands; a package install alone does not prepare embeddings. |
| Companion mode | Say “companion mode on”; later say “companion mode off”. | Wait through the configured idle interval for an opener. While enabled, detected speech goes to the host without another wake phrase. |
| Personalities | Create the small JSON catalog in [client setup](client-setup.md#give-a-client-its-own-personalities), select it in the client wizard, restart. For the browser use the host's `--web-personalities-file`. | List/switch personalities by voice and with the touch menu. The Pi and browser can use different catalogs. |
| Weather | Enable weather in the host wizard and give a town/country. | Ask for the weather; this sends a location request to Open-Meteo, although chat/speech can remain local. |
| Notifications | Follow [notification setup](server-setup.md#push-notifications-with-pushover) to create Pushover credentials and prepare the phone app; save credentials in the host service environment. | Ask for one explicit test notification and check its arrival. This optional service needs an account/internet and may require a purchase. |

The shared menu lists exact control phrases; ordinary questions to the model
are not guaranteed controls. [Memory storage](memory-storage.md) covers export,
derived-index rebuilds, corrections and confirmed forgetting. Context limits
bound what Iris reads for a reply; they do not trim the original source archive.

## I. Start the Pi client without an SSH window

**Pi: SSH terminal in `~/iris-llm`.** Save a working wake or continuous activation
in `client_config.json` first. Enter-to-talk waits for keyboard input and is not
the right choice for a background service.

Run `nano run_client.sh` and save:

```sh
#!/bin/bash
cd "$HOME/iris-llm" || exit 1
exec .venv/bin/python iris.py client --config client_config.json
```

Run `install -d -m 700 ~/.config/iris`, then `nano ~/.config/iris/client.env`.
Save one line `IRIS_DROID_TOKEN=YOUR_HOST_CONNECTION_KEY`, replacing the
placeholder. This is an environment file, not JSON and not a shell script.

```sh
chmod 600 "$HOME/.config/iris/client.env"
mkdir -p "$HOME/.config/systemd/user"
cp systemd/iris-client.service "$HOME/.config/systemd/user/"
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now iris-client.service
journalctl --user -u iris-client.service -f
```

Stop any manually running client first. Ctrl+C leaves the journal viewer; it
does not stop the service. Reboot with `sudo reboot`, reconnect by SSH, and test
the wake phrase, spoken answer and face/touch if enabled. Use
`systemctl --user status iris-client.service` and the journal if it keeps
restarting. USB permissions or a stale device name need fixing, not a second
client process. [Boot troubleshooting](client-setup.md#7-start-the-pi-client-at-boot)
covers user-journal permissions and sound groups. The face is a separate system
unit; the home service is a separate user unit.

Automatic updates are optional. Complete [the Pi updater setup](updates.md)
only after this manual installation survives reboot. Keep a copy of your working
launcher/settings and the exact installed revision (`git rev-parse HEAD`).

## J. Save the working system and return to the host

**Pi and host: terminal and private backup storage.** Keep the client/server
configuration, personality catalogs, prepared voices, model-host settings and
startup files. Keep the connection/home/notification keys through your usual
private backup method. A clone of the Git repository does not include these.

Stop the relevant services and update timers before copying live state. Use the
SQLite backup recipe in [memory storage](memory-storage.md) for the conversation
archive and [home backups](home-automation.md#backing-up-the-home-setup) for the
schedule database. Copy the **whole** Matter pairing directory, home certificate
and private key too. Copy Caddy's private data directory securely if you want to
retain the phone's existing trust after rebuilding the host. Never post these
backups publicly.

Restore first into a separate test directory, inspect/integrity-check the copied
archive, and keep the working original untouched. A live home restore is a
separate deliberate check with services stopped and a harmless device: old
schedules can become active again. The [release checklist](releasing.md) records
reboot, updates, restore and physical checks, with pass/fail evidence.

Return to step 9 of your host route:
[Windows](setup-windows.md#9-keep-the-host-running-after-closing-terminals),
[macOS](setup-macos.md#9-keep-the-host-running-after-closing-terminals), or
[Ubuntu](setup-linux.md#9-keep-the-host-running-after-closing-terminals).
That finishes host startup, updates and the final end-to-end check.
