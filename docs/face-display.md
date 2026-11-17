# Give Iris a face

Iris can put a face on the screen attached to the voice client. It comes on for
the wake phrase or Enter-to-talk, changes while the PC is working, and shows the
expression the model chooses while the reply plays. After the reply, Iris's
face stays on screen. After five minutes without activity, the screensaver
comes on with a few faint flecks drifting down. In hands-free mode with
Silero enabled, it also comes on as soon as the client detects speech in a
follow-up. Without Silero, those follow-ups light up when the recording is sent
to the PC.

Tap the face area to open Iris's menu. Go to **Command list** and pick
a category to see what you can say. You can read the list while you talk to Iris
as usual. **Personalities** shows the choices from this client's
`--personalities-file`. Tap one and Iris will use it the next time you speak.
**Home** has examples of what to ask when the home service is set up. Tap the
**×** in the top right to get back to Iris, or leave the menu alone for three
minutes and it will close by itself.

Update Iris on both the PC and the client, then restart the PC server using your
usual command. There's no extra server flag. A client with a face asks the model
to choose from neutral, curious, amused, concerned, focused, delighted, surprised,
skeptical, sleepy, sad, playful and proud. If the model skips the cue or picks an
unknown expression, the face uses neutral. The cue is
separate from the words Iris speaks and saves in conversation history.

The four faces share those expressions, but each has its own character. Signal
has a softly lit green display, Sprout has warm amber eyes and a softer organic
shape, Rivet has a copper shell and a pale ceramic face, and the android has a
cool blue finish. Their eyes, brows, cheeks and mouth work together, with small
changes in gaze and an occasional double blink.

During spoken replies, mouth movement follows the loudness of the audio actually
playing. It settles during pauses, and the optional startup chirp doesn't make
Iris talk. This works with both streamed and complete replies on the Pi, and
with replies played by the phone's web interface. It's speech-energy animation;
the current speech pipeline doesn't supply phoneme timings, so it doesn't try
to match individual vowel and consonant shapes. Text-only replies still show
the chosen expression. The browser respects your reduced-motion setting.

The Pi uses pre-rendered artwork and a small cache of animation frames at up to
24 frames per second. The phone draws the same artwork on a canvas, with smooth
transitions between expressions. Neither needs a 3D engine or downloaded artwork.
Update the PC and client together to get the full expression range.

Built-in voice and text commands now show a brief result bubble over the face.
“Processing…” means Iris is checking the request; green confirms a completed
change, and amber reports an error or missing confirmation. Some controls need a
further choice or confirmation, so those show an informational response instead.
The bubble disappears after about six seconds. **Companion on** stays visible
while the face is awake, so you don't have to remember the last spoken reply.
These indicators work on the native Pi display and in the phone's web interface.
The command list also has touch buttons for controls that need no extra values.
Clear context and Delete previous ask for confirmation before running. See
[voice commands and confirmations](voice-commands.md) for examples.

## Choose a face

While Iris's face is on screen, tap the smiley in the top left to cycle through
the faces. The new face stays up for a few seconds so you can see what you
picked. The Pi and its local preview share a choice and remember it when the
client restarts.

Add `--face signal` to your usual client command to enable the display and
pick its starting face. You can also start with `humanoid-android`, `sprout`
or `rivet`. Once you've used the button, Iris starts with your saved choice. Add
`--face-idle black` for a plain black screensaver, or use `--face-idle snow`
for the default drifting flecks. In the browser preview, reduced motion also
switches the idle screen to plain black.

**Raspberry Pi OS or Linux (Bash)**, from the checkout with your client
environment active and the connection key already set:

```bash
python iris.py client --server http://192.168.1.10:8765 --face signal
```

For wake mode or a client started by systemd, add the face options to the
existing command in `run_client.sh` so you keep your device and wake settings.
After saving, run `systemctl --user restart iris-client.service`.

## Boot straight into the face on Raspberry Pi OS Lite

The direct display uses the Pi's KMS graphics driver and scales the artwork to
the active display mode. This works with an 800 × 480 DSI panel or a 1024 × 600
HDMI panel.
Linux and the existing voice client still boot as usual. The display service
takes over the first local console when it starts, while SSH remains available.
There's no graphical login to set up.

From SSH, use a Pi that already boots to the console. If you have Raspberry Pi
OS Desktop, you can select **Console** under boot options in `sudo raspi-config`
and reboot. Make sure your normal Iris voice client is set to
[start at boot](client-setup.md#7-start-the-pi-client-at-boot) with a `--face` option.
The display can start while the voice client is still coming up.

Run these **Raspberry Pi OS (Bash)** commands from the Pi checkout:

```bash
sudo apt update
sudo apt install python3-pygame
sudo install -m 0644 systemd/iris-face@.service /etc/systemd/system/iris-face@.service
sudo systemctl daemon-reload
sudo systemctl mask --now getty@tty1.service
sudo systemctl enable --now "iris-face@$USER.service"
```

The service uses the Pi's system Python for its SDL graphics library. Your voice
client keeps using its own Python environment. If the checkout is somewhere
other than `~/iris-llm` under `/home`, edit the installed unit's
`WorkingDirectory` before enabling it. The display expects the default face port
8767. If you changed it on the client, use the same `--port` in the unit's
`ExecStart`.

The display starts in black and picks up the head and expressions as soon as the
voice client is ready. Check the driver and resolution from SSH:

```bash
sudo journalctl -u "iris-face@$USER.service" -b -n 30
```

For the 1024 × 600 HDMI panel, look for `Iris display: kmsdrm, 1024 × 600`.
If SDL picks the wrong DRM card, `ls /sys/class/drm` shows which card has the
HDMI or DSI connector. Add `--kms-card N`
to the unit's `ExecStart` for that card and reload the unit. Keep the Pi's normal
KMS driver enabled. See [Raspberry Pi's Touch Display setup](https://www.raspberrypi.com/documentation/accessories/display.html)
for connection and rotation.

Masking the local login prompt reserves tty1 for Iris. To take the display
service out of startup and get that prompt back, run from SSH:

```bash
sudo systemctl disable --now "iris-face@$USER.service"
sudo systemctl unmask getty@tty1.service
sudo systemctl start getty@tty1.service
```

### 1024 × 600 HDMI touchscreens

Connect and power the display before booting the Pi. HDMI carries the picture.
Connect the display's USB touch port to the Pi with a USB data cable.
If Iris was already taking over the screen on boot, copy the updated service
file from the checkout so SDL can read the touchscreen input. If you edited
the installed service for your display, add `input` to its
`SupplementaryGroups` line instead. Then reload systemd and restart the display
and voice client so both pick up the menu.

```bash
sudo install -m 0644 systemd/iris-face@.service /etc/systemd/system/iris-face@.service
sudo systemctl daemon-reload
sudo systemctl restart "iris-face@$USER.service"
systemctl --user restart iris-client.service
```

Start with automatic detection. If the journal shows a different resolution
or the display has no picture, run `kmsprint -l` over SSH to check which modes
the display reports. Follow the panel maker's timing instructions if needed.
For a panel that supports 1024 × 600 at 60 Hz, an example KMS override is
`video=HDMI-A-1:1024x600M@60` on the existing single line in
`/boot/firmware/cmdline.txt`. Use `HDMI-A-2` for the second output and reboot
after a change. See [Raspberry Pi's display settings](https://www.raspberrypi.com/documentation/computers/configuration.html#configure-display-settings).

You can also check the framing in a desktop window before the panel arrives.
With Pygame installed for the Python you are using, run
`python -m iris_droid.face_console --window 1024x600 --demo rivet` from the
checkout. That command is the same in Windows PowerShell and Linux Bash.

## Browser preview

Open `http://127.0.0.1:8767/` in the browser on the Pi's desktop and use
fullscreen, or run this from a terminal in the Pi's desktop session:

```bash
chromium --kiosk --no-first-run http://127.0.0.1:8767/
```

To start the face automatically, enable your normal Iris voice client service
at boot. If your Pi desktop uses labwc, add this to
`~/.config/labwc/autostart` so the browser waits for the client at login:

```bash
(
    until curl -fs --max-time 2 http://127.0.0.1:8767/state >/dev/null; do
        sleep 1
    done
    exec chromium --kiosk --no-first-run http://127.0.0.1:8767/
) &
```

The browser reconnects after a voice client restart. If the desktop blanks the
display, turn off screen blanking in Raspberry Pi OS if you want Iris visible
without touching the display. The [Raspberry Pi kiosk guide](https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/)
covers desktop login and screen blanking.

**Windows (PowerShell)**, from the checkout with your client environment active
and the connection key already set:

```powershell
python .\iris.py client --server http://192.168.1.10:8765 --face signal
```

In a second PowerShell window, run `Start-Process "http://127.0.0.1:8767/"`
and use your browser's fullscreen shortcut. On either platform, add `--text`
to try the display without audio. Iris holds the face briefly after each typed
reply so you can see the expression.

The original [Raspberry Pi 7-inch Touch Display](https://www.raspberrypi.com/documentation/accessories/display.html)
is 800 × 480. The face scales to the browser window in either orientation.
The face page needs a browser running on the display you want to use.

The page uses the client's loopback address and receives the selected head,
current activity and expression. The usual authenticated connection to the
PC still handles the recording and spoken reply. If port 8767 is in use,
choose another one with `--face-port` and open that port in the browser.
