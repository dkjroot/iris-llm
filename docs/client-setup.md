# How to set up the client

Do this on the laptop or Raspberry Pi with the microphone and speaker, or in the same checkout as the server if you're using one computer. The client records your speech and plays replies; Whisper, Kokoro and the chat model run on the server. You don't need to run `configure` or download those models on a separate client.

Use Python 3.11–3.13 (I'd start with 3.12), Git, and a working microphone and speaker. For a Pi, use a 64-bit OS; it isn't being asked to load the PC's models.

## 1. Install the client

Install Git first if needed. On Debian-based Linux or Raspberry Pi OS, use `sudo apt update` followed by `sudo apt install git`; on Windows or macOS, use your usual Git installation. Then get the repository (shared commands for all platforms):

```sh
git clone https://github.com/dkjroot/iris-llm.git
cd iris-llm
```

If you've already got the repository, open a terminal in that checkout instead. There's no need to clone another copy. If its `.venv` already exists, activate it rather than creating it again.

### Windows (PowerShell)

With Python 3.12 and Git installed, open PowerShell in the repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

If PowerShell won't activate the environment, use `.\.venv\Scripts\python.exe` in place of `python` below. You don't need to change your execution policy just to run Iris. In Command Prompt, you can activate it with `.venv\Scripts\activate.bat` instead.

### macOS (Terminal)

With Git and Python 3.12 installed, install PortAudio with Homebrew before building the PyAudio microphone adapter:

```sh
brew install portaudio
python3.12 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

Allow microphone access for the terminal or application you're using to launch the client when macOS asks for it. If pip stops with `fatal error: 'portaudio.h' file not found`, run the Homebrew command above and retry the client package installation below in your activated environment. You don't need to recreate the environment or install the full Xcode application for that error; the compiler has already got far enough to look for the header.

### Linux and Raspberry Pi OS (Bash)

On a Debian-based system with a supported `python3`, install the prerequisites, then create the environment in the repository:

```sh
sudo apt update
sudo apt install git python3-venv python3-dev build-essential portaudio19-dev libportaudio2 alsa-utils
python3 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

Check `python3 --version` first. If your distribution ships a Python outside 3.11–3.13, install a supported interpreter and use its matching venv/development packages. Other distributions need their equivalents of the packages above.

### Install the Python packages (all platforms)

With the environment active:

```sh
python -m pip install -e ".[microphone]"
```

If you're sharing the server's environment on one computer, this adds microphone support to the packages already installed for the server.

If you prefer uv, use `uv sync --locked --python 3.12 --extra microphone` instead of the venv/pip steps. Run subsequent commands with `uv run --no-sync python` instead of `python`. If this environment also runs the server, use `uv sync --locked --python 3.12 --extra microphone --extra whisper --extra kokoro` so all three extras stay installed. You can pick pip or uv; you don't need to use both.

## 2. Set the connection key

Use the **same `IRIS_DROID_TOKEN` value you chose for the server**, rather than generating another one. Set it in the terminal where you'll start the client, with its environment active.

**Windows PowerShell:**

```powershell
$env:IRIS_DROID_TOKEN = 'paste-your-chosen-key-here'
```

**Windows Command Prompt:**

```bat
set "IRIS_DROID_TOKEN=paste-your-chosen-key-here"
```

**macOS zsh, Linux Bash, and Raspberry Pi OS:**

```sh
export IRIS_DROID_TOKEN='paste-your-chosen-key-here'
```

This applies to the current terminal and programs launched from it. Set it again in a new terminal, or put it in your IDE or service's launch environment. Keep the key out of committed files. A client on the same computer still needs this key in its own terminal. The client doesn't need `IRIS_API_KEY`; any model endpoint credentials belong on the server.

## 3. Test the connection with text

Leave the Iris server and its model server running. For a client on another computer, use the PC's actual LAN address (shared command for all platforms):

```sh
python iris.py client --server http://192.168.1.10:8765 --text
```

Replace `192.168.1.10` with your PC's address. This is Iris's port `8765`, not the model server's address or port. `0.0.0.0` is a listening address, not an address to give the client; `127.0.0.1` always means the machine you're on. The client doesn't need an incoming firewall rule. Guest Wi-Fi sometimes keeps devices apart even when they're connected to the same router.

If the client and server are on the same computer, open a second terminal in the repository, activate the same environment, set the same key, and run:

```sh
python iris.py client --server http://127.0.0.1:8765 --text
```

Type a message and check that a reply prints. This goes through Iris and the chat model but skips microphone capture, transcription and synthesis. Type `exit` to finish the text test, then choose the audio devices below. For all subsequent client commands, use your PC's LAN address for a separate machine, or `http://127.0.0.1:8765` when both halves run on one computer.

## 4. Set up listening and audio devices

The client records mono audio at the microphone's reported default sample rate, unless you override it with `--microphone-rate`, and converts it to 16 kHz for the server. For playback, it checks whether the speaker accepts the audio's rate; if necessary it converts the samples to the speaker's reported default rate, keeping the duration and pitch. A USB device running at 44.1 or 48 kHz doesn't need to support the speech models' rates directly. Wake recognition uses the microphone's actual rate too.

All the commands in this section run on the **client**, with its Python environment active. You don't need to run `configure` or download Whisper or Kokoro on the Pi; those belong on the PC server. Keep the same `IRIS_DROID_TOKEN` and server address that worked for your text test.

The client defaults to Enter-to-talk. Use `--activation continuous` to listen whenever it's your turn. These are client choices; changing the server config doesn't change when the microphone opens. `--text` skips all audio, so a working text conversation confirms the connection but doesn't test recording or playback.

### Choose the microphone and speaker

These commands are shared by Windows PowerShell, macOS and Linux Bash, including Raspberry Pi OS:

```sh
python iris.py client --list-microphones
python -m sounddevice
```

The first command lists PyAudio devices for `--microphone`. The second lists sounddevice devices for `--speaker`. Look for one with output channels (`out`). Choose names from the respective lists. On a Pi, USB audio devices can come back in a different order after a reboot, so use names in the client service.

This command works in PowerShell and Bash. Replace the address and the quoted names with yours.

```sh
python iris.py client --server http://192.168.1.10:8765 --microphone 'USB Microphone' --speaker 'USB Speaker'
```

Iris looks up the current indices at launch. A full name takes precedence, and part of a name works if it identifies one device in the right direction. On Linux the card number in a suffix such as `(hw:3,0)` can change, so Iris ignores that part of the suffix when matching names. If several devices match, use a more specific name. Indices still work, and leaving out either option uses the corresponding default. The speaker plays both the listening cue and Iris's reply.

### Save the client settings

Once you've got the client working, you can save its settings so you don't have
to keep typing the same switches. Run these from the Iris checkout on the client.

```sh
python configure_client.py
python iris.py client
```

The first command creates `client_config.json` in the current directory. You can
run it again to change settings, or edit the JSON yourself. The names match the
switches with underscores in place of hyphens, so `--pause-threshold 2.5` becomes
`"pause_threshold": 2.5` and `--no-speech-stream` becomes `"no_speech_stream": true`.
Git ignores this file. Keep the connection key in `IRIS_DROID_TOKEN`.

The client checks for this file in the directory you launch it from. Use
`python iris.py client --config /path/to/client_config.json` to pick another file.
Relative model paths are resolved from the launch directory. You can still pass
switches like `--server` or `--microphone` to override a saved value for one run.
One-off commands like `--text` and `--test-microphone` still go on the command line.

### Give a client its own personalities

You might want Iris to chat one way on a little robot around the house and
another way on the one that plays chess with you. On each client, make a
`personalities.json` file with the names and a short prompt for each personality.

```json
{
    "friend": "Be warm and easygoing, like a friend with time for a chat.",
    "scientist": "Be curious and precise. Explain what we can test and what we're guessing.",
    "philosopher": "Be reflective and patient. Look for the interesting question underneath."
}
```

Add `"personalities_file": "personalities.json"` to `client_config.json`, or run
`python iris.py client --personalities-file personalities.json`. Relative paths
start from the directory you launch the client in. Use the first entry for the
personality you want at startup.

Say or type **“list personalities”** to hear what this client has and which one
it's using. Say **“switch to personality philosopher”** to change it for the
rest of the run. That choice carries through ordinary conversation, companion
mode and the little conversations Iris starts herself. If you restart the client,
it reads the file again and starts with the first entry.

The client sends the active snippet to the Iris server with each turn. The
server adds it to Iris's role instructions for the chat model. Update both the
server and the client before trying this.

### Control the speaker volume by voice

On the Pi, you can say “set volume to 50 percent”, “volume up”, “volume down”,
“what's the volume?”, “mute the speaker” or “unmute”. Up and down change the
volume by ten percentage points. You can also say “turn the volume down by
five percent”. Numbers work as words too, so “set volume to fifty percent”
does the same thing.

Enable this on the client with `--volume-backend alsa`. Install `alsa-utils`
if `amixer` isn't already available (`sudo apt install alsa-utils` on Raspberry
Pi OS). Choose the card and playback control that drive your speaker:

```sh
cat /proc/asound/cards
amixer -c 'Device' scontrols
amixer -M -c 'Device' sget 'PCM'
python iris.py client --server http://192.168.1.10:8765 --speaker 'USB Speaker' --volume-backend alsa --alsa-card 'Device' --alsa-control 'PCM'
```

Use the ALSA card ID in square brackets in `/proc/asound/cards`. For example,
if the line begins with `3 [Device         ]`, use `Device`. ALSA finds the
card by that ID even when its number changes. The mixer control might be
called `Master`, `PCM` or `Speaker`. Use the name shown by `amixer`. The
speaker name and ALSA card ID come from different lists, so check that both
refer to the same output. A numeric ALSA card still works. Without overrides,
Iris uses the default ALSA card and the `Master` control.

The recording goes to the server for recognition as usual. Iris recognises
these whole commands directly, sends an instruction back to the client and
reads back the actual mixer level for spoken confirmation. The chat model
isn't involved, and local wake detection works as before. Both server and
client need to be updated. Volume control is disabled by default, so other
platforms keep working without ALSA installed.

This changes the same mixer you see in `alsamixer`, including other audio
using that control. Iris uses `amixer -M` so its percentages follow
[ALSA's mapped scale](https://github.com/alsa-project/alsa-utils/blob/master/amixer/amixer.1),
as `alsamixer` does. Hardware can round the requested level. Setting a level
leaves mute unchanged; say “unmute” separately if needed. Some devices have no
mute switch, in which case Iris reports that it couldn't make that change.
After muting or setting zero, you won't hear confirmation until you unmute
or raise the volume, but wake detection and the microphone still work.

When you set the volume or turn it up or down through Iris, the client saves
the speaker level it reads back from the mixer. It restores that level the
next time it starts with the same ALSA card and control, before playing any
cues. The setting lives on the Pi under `~/.local/state/iris/volume/` (or
`$XDG_STATE_HOME/iris/volume/` if set), and Iris leaves the mute switch as it
finds it. If you set up your own `alsactl restore` command for this speaker,
you can stop using it once you've chosen a volume through Iris.

If the mixer fails, Iris reports the failure rather than claiming success.
Check the `amixer` command above as the same user who runs the client. If the
connection fails after a change, the client prints the result it read locally
and doesn't resend the change automatically. You can ask for the current
volume once the connection is back.

Other platforms can add a backend by implementing `VolumeBackend.execute`
in `iris_droid/volume.py` and registering it in `make_volume_backend` and the
client's `--volume-backend` choices. The command recognition, transport and
spoken confirmation are shared across backends.

### Raspberry Pi: ALSA card 3, device 0

If `arecord -l` and `aplay -l` show **card 3, device 0**, the ALSA hardware address is `hw:3,0`. That is a different kind of number from an Iris device index: don't automatically pass `--microphone 3 --speaker 3`. Iris currently accepts integer indices for these options, rather than ALSA addresses such as `hw:3,0` or `plughw:3,0`.

Run the two Python listing commands above on the Pi. Look for your device's name and `(hw:3,0)` in each list. For example, you might see:

Microphone list:

```text
2: USB Audio Device: Audio (hw:3,0)
```

Speaker list:

```text
7 USB Audio Device: Audio (hw:3,0), ALSA (1 in, 2 out)
```

With those example lists, use `--microphone 2 --speaker 7`, as in the launch command above. Your actual indices may differ, even though the ALSA card and device are the same. Recheck the lists after reconnecting USB audio devices or changing the audio setup.

If you need to check the ALSA names again, these are **Linux / Raspberry Pi OS** commands:

```sh
arecord -l
aplay -l
```

Both tools come from `alsa-utils` (`sudo apt install alsa-utils` on Raspberry Pi OS). An ALSA recording/playback test is a useful first step, but Iris still needs the right devices selected through its Python libraries. You can select them explicitly without changing the whole system's default audio device.

### Check playback, then try a voice turn

To test the chosen speaker without contacting the server, this shared command plays Iris's packaged listening cue through the same playback function the client uses. Replace `7` with your sounddevice output index:

```sh
python -c "from importlib.resources import files; from iris_droid.client import play_wav; play_wav(files('iris_sounds').joinpath('bleep.wav').read_bytes(), device=7)"
```

Then launch the voice client with both device options and your PC's address. Press Enter, wait for the listening cue, then say a short sentence and pause. The client stops recording after silence, sends the recording to the PC, prints the transcript and reply, and plays the reply. Enter starts a turn; it isn't a key you hold while talking. Say `exit` or press Ctrl-C to finish.

On Linux, device enumeration can print ALSA `Unknown PCM` messages and warnings that JACK isn't running before showing the list. Those messages alone don't establish that your selected device failed; check the device list and the result of the playback or capture attempt. `python -m sounddevice` only lists devices; adding an index to that command doesn't select or test one.

If that doesn't work:

- **No listening cue:** try the playback check above. Check the output index, speaker volume and mute controls. On Raspberry Pi OS, `alsamixer -c 3` opens the mixer for card 3, if it has mixer controls. Check that `--no-sounds` is absent and look for a feedback warning; optional cue failure disables feedback without preventing capture.
- **Cue plays, but no speech is heard:** check the microphone index and input gain/mute, and speak after the listening cue. `Didn't hear speech. Try again.` means speech didn't start within the listening timeout. An HTTP `422` means the recording reached the PC but its recogniser couldn't understand it.
- **Invalid device, channel or sample-rate errors:** recheck both device lists. To inspect your output device, run `python -c "import sounddevice as sd; print(sd.query_devices(1))"`, replacing `1` with your speaker index. `default_samplerate` is the rate the client uses if the speaker rejects the cue or reply's original rate. If this still fails, check that the selected devices support mono capture/playback and that their reported default rates work. An ALSA test using `plughw:3,0` can apply format conversion that a direct `hw:3,0` entry doesn't provide. An appropriate `default` or `sysdefault` entry may help if it routes to your device. Changing `--listen-timeout` won't fix a format error. The [ALSA configuration guide](https://www.alsa-project.org/wiki/Asoundrc) explains hardware and conversion plugins.
- **Device busy:** stop any `arecord`, `aplay` or other audio test still using the hardware before starting Iris. Run the client as your normal user in the same environment as the successful tests, rather than trying `sudo` as an audio fix.
- **Transcript and reply print, but no spoken reply:** repeat the playback check with the same speaker index. A text-mode run never plays speech; make sure `--text` is absent. An HTTP `503` points to the PC's speech service instead.

### Feedback sounds on the client

The client plays feedback through the device selected with `--speaker`, including when the server is on another computer. The server doesn't need a speaker. The cues tell you which part of a turn Iris has reached:

| Sound | Meaning |
| --- | --- |
| Subtle rising chirp | Ready to start. In Enter mode, press Enter. In continuous mode it plays once at startup; in wake mode it announces waiting for the wake phrase. |
| Brief listening bloop | Start your sentence now. In wake mode this confirms the wake phrase was accepted. It doesn't repeat between turns in an open hands-free conversation. |
| Short higher chirp | Recording has finished in Enter mode or the local microphone test. Hands-free turns skip this cue. |
| Quick quadruple chirp | A command was accepted: “clear context” keeps the conversation open; “stop listening” returns to wake waiting. |
| Repeating low working pulses | Waiting for the server to transcribe, ask the model and generate speech. These stop before the spoken reply, or if the turn fails or you stop the client. |
| Three short low pulses | A turn/audio service failed, or an Enter-to-talk/local test heard no speech. Ordinary hands-free listening timeouts and unrecognised recordings don't play an error cue. |

When starting a wake conversation, wait for the ready chirp before saying the wake phrase, then wait for the listening bloop before your first sentence. Once the conversation is open, or companion mode is on, Iris quietly returns to listening after each reply. You don't need to wait for another cue or repeat the wake phrase. Continuous mode also listens quietly between turns. Feedback isn't played over an open microphone. Finishing a conversation with `exit` stops the client rather than announcing another turn.

Feedback is on by default. Add `--no-sounds` to your client start script to disable the optional cues and working sound. The voice-cancel acknowledgement still plays so you know the turn was rejected; spoken replies, `--echo-recording` and local-test playback still work. Text mode is always silent. If a feedback asset or cue playback fails, the client prints a warning and continues without feedback; a failure to play the actual reply or diagnostic echo is still an audio error.

The cues are original generated WAV files, so they don't need FFmpeg, downloads or a remote sound service. See [the sound guide](../iris_sounds/README.md) for the filenames, generator and replacement advice. The busy sound covers the whole server request; it doesn't report individual STT, chat or synthesis progress.

### Reject kitchen noise with local speech detection

If chopping, crockery or other background noise keeps triggering recordings, try the optional [Silero speech detector](https://github.com/snakers4/silero-vad). It checks whether the audio sounds like speech, rather than relying on its volume. This runs on the client and keeps the existing PC recognition, chat model and tools. It is an option for testing on your own microphone, rather than a guarantee that every kitchen sound will be rejected.

On the Pi, open the repository and use its Python environment. Install the optional runtime and explicitly download the pinned model once:

```sh
source .venv/bin/activate
python -m pip install -e ".[microphone,vad]"
python iris.py client --download-vad-model .cache/vad/silero-v6.2.onnx
```

This uses ONNX Runtime without installing PyTorch or the PC's speech models. The download is checked against a pinned SHA-256 checksum; ordinary startup only loads the local file. With uv, use `uv sync --locked --python 3.12 --extra microphone --extra vad` instead of pip, and `uv run --no-sync python` for subsequent Python commands. Include your server extras too if both halves share this environment.

Add this option to your existing client command or `run_client.sh`, keeping your server address, wake settings and audio devices:

```sh
--vad-model .cache/vad/silero-v6.2.onnx
```

Then restart the user service with `systemctl --user restart iris-client.service`, or test in a terminal after stopping the service. You can check capture locally first, replacing the example device indices:

```sh
python iris.py client --test-microphone --microphone 2 --speaker 7 --vad-model .cache/vad/silero-v6.2.onnx
```

The detector keeps your microphone's native capture rate and buffer settings, converts audio continuously to 16 kHz, and checks short windows for speech. A short buffer before confirmed speech protects the start of words. Loud sounds classified as non-speech don't prolong your sentence. `--pause-threshold` still controls the gap after speech that ends a turn (default 1.2 seconds); try `--pause-threshold 0.8` if you want a shorter gap, or increase it if thinking pauses cut you off. This detects pauses in speech, rather than deciding whether you've finished your thought.

`--vad-threshold` controls how confidently speech must be detected to start a turn (default `0.5`, greater than 0 and less than 1). Higher values reject more sounds but can miss quiet speech; lower values admit more sounds. Energy calibration, `--energy-threshold` and `--calibration-duration` aren't used with VAD. Remove `--vad-model` to return to energy-based capture; the quieter hands-free feedback still applies.

While waiting, rejected sounds don't start working feedback or restart the companion timer. Sounds the detector mistakes for speech can still reach the server, but an unrecognised recording doesn't play an error cue in hands-free mode. Actual speech from a television or another person is still speech: this isn't speaker identification or a check that you're addressing Iris. Try speaking while doing kitchen tasks as well as making noise without speaking, and check for lost words and delayed endings. Pi performance and real microphone accuracy need testing on the Pi.

### Spoken formatting and missing opening words

Iris removes paired Markdown emphasis markers such as `**bold**` and `*italic*` from the text sent to Kokoro. The printed reply and chat history keep the model's original text. This is a small speech-rendering step; it doesn't remove meaningful operators such as `2**3` or rewrite the reply's words.

For playback, the client asks PortAudio for its more conservative `high` output latency and puts 0.3 seconds of silence at the start of the **same audio stream** as each spoken reply. A sleep before opening the speaker stream doesn't provide that lead-in. These output settings are separate from microphone buffers and recording rates. If PortAudio reports a playback underrun, the client prints a warning because audio may have been lost.

If an opening word is still missing, save a reply so you can compare the same audio file through different playback routes. Add this option to your existing voice-client command or start script (shared syntax for Windows PowerShell, macOS and Linux Bash):

```sh
--save-reply .cache/iris-reply.wav
```

This is opt-in: it writes the **unmodified server WAV**, before client playback padding, and replaces that file on each spoken reply. It contains conversation audio, so keep it local and delete it after debugging. The suggested `.cache` directory is ignored by Git. Ordinary conversations still save nothing.

Stop the running client before testing the speaker. These shared commands play that file without a server, key or microphone, using your selected output index:

```sh
python iris.py client --play-wav .cache/iris-reply.wav --speaker 7 --speech-lead-in 0
python iris.py client --play-wav .cache/iris-reply.wav --speaker 7 --speech-lead-in 0.3
```

Replace `7` with your speaker index. The first run adds no silence; the second uses the normal reply lead-in. `--speech-lead-in` accepts 0–2 seconds and can also be put in the ordinary client start script. Compare the first word, not just whether sound plays. On Linux you can also play that same file through ALSA, replacing the card/device address with yours:

```sh
aplay -D plughw:3,0 --buffer-time=500000 .cache/iris-reply.wav
```

If ordinary playback routes preserve the first word but Iris playback loses it, concentrate on output startup. If the word is missing in the file through every route, inspect the generated speech and the server's printed reply instead. Local tests with Heart and Emma retained opening words through synthesis and preparation, but that doesn't establish what every voice and speaker will do.

For a further output-startup experiment, add `--speech-primer` to the file test or your voice-client command. It puts the soft ready chirp, then the configured silence, then the speech into one playback stream. This is off by default and is separate from `--no-sounds`; use it to see whether an audible onset helps a speaker that clips the start. It doesn't insert extra spoken syllables or change the text sent to Kokoro. There is no supported text style-tag control in the installed Kokoro adapter; its voice style comes from the selected voice data.

### Start with a local microphone test

If speech recognition is poor, start by listening to the recording. Stop any other client or audio test using the microphone, activate the client environment, and run this shared command on Windows PowerShell, macOS or Linux Bash:

```sh
python iris.py client --test-microphone --microphone 2 --speaker 7
```

Replace `2` and `7` with indices from your own microphone and speaker lists. Keep quiet until the listening cue, then say a complete sentence with a natural pause in it. The client records one turn, prints the capture rate, buffer size, speech threshold and recorded duration, then plays it back locally. It uses the listening and recording-finished cues but no working sound because it makes no server request. It needs no server, connection key or downloaded speech model, and saves no file. This is a plain recording test: `--activation` and wake-model options don't make it wait for a wake phrase. Ctrl-C cancels it. `--text` can't be combined with this test.

Check that the beginning, middle and end are all present, that the speed sounds natural, and that your voice is clear rather than very quiet or distorted. The duration includes retained silence around the sentence, so it won't be an exact measure of speaking time. If the test fails to hear speech, check the selected input, hardware gain and the sensitivity controls below.

Once that sounds right, use [the recording echo](#hear-what-iris-recorded-before-sending-it) during a real conversation. The local test sends nothing; echo mode sends the recording after playback. A clean, complete echo with an inaccurate transcript points towards the server's recogniser, language or model settings. An incomplete or damaged echo means capture or playback still needs attention before changing Whisper.

### Client recording controls

These options apply to the local microphone test and to normal voice turns, including continuous and wake activation. Keep them in your client start command or script; they aren't server settings. Try one change at a time and compare the same sentence with the local test.

| Option | Default | When to change it |
| --- | --- | --- |
| `--microphone-rate HZ` | The selected microphone's reported default rate | Override a misleading device default, or select a rate you've verified the microphone supports, such as `48000`. Allowed range: 8,000–192,000 Hz; being in that range doesn't mean your hardware supports it. |
| `--microphone-buffer FRAMES` | `8192` | Increase for input overflow; reduce for finer capture timing if the device remains reliable. Allowed range: 256–65,536 frames. |
| `--pause-threshold SECONDS` | `1.2` | Increase if a thinking pause ends your sentence. Allowed range: 0.5–10 seconds. |
| `--listen-timeout SECONDS` | `10` | Allow more time for speech to begin after the cue. Allowed range: greater than 0, up to 300 seconds. |
| `--phrase-time-limit SECONDS` | `60` | Allow longer messages. Allowed range: greater than 0, up to 300 seconds; the server's `phrase_time_limit` must allow the same length. |
| `--calibration-duration SECONDS` | `0.6`, extended if needed to read at least one input buffer | Sample background noise for longer before the cue. Stay quiet during calibration. Allowed range: 0.1–10 seconds; actual calibration is rounded to audio buffers. |
| `--energy-threshold LEVEL` | Calibrated from background noise; reused within an open wake conversation | Set speech sensitivity manually when automatic calibration doesn't suit the room. A value greater than 0, up to 32,768, skips calibration. Lower values detect quieter speech but can also detect noise; higher values require louder speech. This isn't a microphone gain or volume control. |

The recording rate is a hardware choice; the outgoing format stays mono, 16-bit PCM at 16 kHz so the server receives the same format from every client. Increasing the capture rate doesn't change the Whisper model. Wake recognition is told the actual capture rate. Input is currently mono; a device that only supports multichannel capture needs a suitable mono route from the operating system.

There is no fixed ALSA card number, device index, input rate or speech energy level chosen for a particular microphone. The default buffer is a general reliability/timing tradeoff, not a hardware-specific preset: 8,192 frames is about 186 ms at 44.1 kHz, 171 ms at 48 kHz, or 512 ms at 16 kHz. A large buffer at a low rate makes both calibration and speech-boundary decisions coarser. Leave the rate automatic unless testing gives you a reason to override it.

For example, to test a microphone known to support 48 kHz while allowing two-second pauses (shared command for all platforms):

```sh
python iris.py client --test-microphone --microphone 2 --speaker 7 --microphone-rate 48000 --pause-threshold 2
```

If you need manual sensitivity, first look at the speech threshold printed by the local test or `--echo-recording`, then try a lower or higher value relative to that. A threshold that works for one microphone and input-gain setting needn't work for another. The threshold stays fixed while you speak so sustained speech doesn't get mistaken for rising background noise.

### Hear what Iris recorded before sending it

Add `--echo-recording` to your client start script to hear each recording before it goes to the server. This works with Enter, continuous and wake activation. For example (shared command for all platforms, using the earlier example device indices):

```sh
python iris.py client --server http://192.168.1.10:8765 --microphone 2 --speaker 7 --echo-recording
```

After you speak, the microphone closes, the client prints the recording's duration and plays the exact outgoing recording through your selected speaker. Once playback finishes, it sends that same recording automatically and waits for Iris's reply. Ctrl-C during the echo stops the client before sending that turn. A playback failure also stops it before sending. Nothing is saved to disk, and `--text` skips the echo.

If the echo contains only the first part of your sentence, the recording was cut short before the server received it. If the whole sentence sounds clear but the printed transcript is incomplete, investigate recognition on the server. This makes it easier to check the audio before changing models or microphone settings. Remove `--echo-recording` when you've finished checking; it adds playback time to every voice turn.

### Recordings that stop halfway through a sentence

Without `--vad-model`, the client briefly calibrates to background noise before capture, then keeps that threshold fixed for the turn. In wake mode, it reuses the threshold throughout the open conversation, saving another calibration and microphone setup between replies. It calibrates again when it returns to waiting for the wake phrase. If the room noise changes during a conversation, say “stop listening” and wake Iris again to refresh it. Enter and continuous modes still calibrate each turn. Keep quiet during calibration and speak after the listening cue. The threshold doesn't keep rising with your voice while you're talking.

By default, 1.2 seconds of audio below the calibrated threshold ends the recording. If you pause to think while speaking, add `--pause-threshold 2` to your existing client command to allow a longer gap. This option works with Enter, continuous and wake activation, on all platforms. Keep `--echo-recording` enabled while checking that your whole sentence is captured.

`--pause-threshold` accepts 0.5–10 seconds. A longer value also means a longer wait before Iris sends your finished message. `--listen-timeout` only controls waiting for speech to begin; increasing it won't prevent a sentence ending early. `--phrase-time-limit` is the overall turn cap, so check that too if recordings consistently stop at the same duration.

### Fast or crackly microphone recordings

The client uses an 8,192-frame microphone buffer (about 186 ms at 44.1 kHz), giving capture more breathing room than the speech library's 1,024-frame default. It also checks for input overflow: if PortAudio reports that samples were lost, the client discards the current recording and stops with an error instead of sending damaged speech to the server. These checks apply while waiting for the wake phrase as well as while recording a message.

If you get `Microphone input overflow`, try adding `--microphone-buffer 16384` to your existing client command. This is a client option, shared across Windows, macOS and Linux; it doesn't change the server configuration. For example, using the device indices from the earlier example:

```sh
python iris.py client --server http://192.168.1.10:8765 --microphone 2 --speaker 7 --microphone-buffer 16384
```

The setting is in frames, not milliseconds; larger buffers make capture less responsive to speech starting and stopping. The allowed range is 256–65,536 frames. Check for other processes keeping the client CPU busy too. A larger buffer is something to try, not a guarantee that every source of crackling is an overflow.

If a native ALSA recording sounds clean but an Iris recording sounds shortened or crackly, compare both recordings through the same playback route. Check offline sample-rate conversion separately from microphone capture before changing the Whisper model. Keep any diagnostic recordings local; ordinary client operation doesn't save them.

### Linux / Raspberry Pi OS: compare ALSA with Python capture

If you still hear fast, shortened or crackly audio, compare capture and conversion separately. These commands are for Linux Bash and deliberately create local test WAV files under `/tmp`. Use a non-private test sentence and remove the files when you've finished.

First check `arecord -l` and `aplay -l` for the card and device. This example assumes card 3, device 0 supports 44.1 kHz, mono, 16-bit recording; replace the address and rate with ones supported by your device. `hw:3,0` is an ALSA address, not an Iris index:

```sh
arecord -D hw:3,0 -f S16_LE -r 44100 -c 1 --buffer-time=500000 -d 8 /tmp/iris-native.wav
aplay -D plughw:3,0 --buffer-time=500000 /tmp/iris-native.wav
```

Speak through the eight-second recording. The buffer setting here is 500,000 **microseconds**, or half a second; Iris's `--microphone-buffer` uses **frames** instead. Watch for `overrun` during recording or `underrun` during playback. ALSA tools come from the `alsa-utils` package.

Now play that **same file** through Python, replacing `7` with your sounddevice speaker index:

```sh
python -c "from pathlib import Path; from iris_droid.client import play_wav; play_wav(Path('/tmp/iris-native.wav').read_bytes(), device=7)"
```

If ALSA sounds clean but Python playback doesn't, investigate output-device selection and playback before blaming the microphone. If both sound clean while `--test-microphone` sounds damaged, check Python capture and conversion. To test conversion alone, use the clean ALSA file without reopening the microphone:

```sh
python -c "from pathlib import Path; import speech_recognition as sr
with sr.AudioFile('/tmp/iris-native.wav') as source:
    audio = sr.Recognizer().record(source)
Path('/tmp/iris-converted.wav').write_bytes(audio.get_wav_data(convert_rate=16000, convert_width=2))"
aplay -D plughw:3,0 --buffer-time=500000 /tmp/iris-converted.wav
```

If the converted file is clean too, live Python capture is the remaining difference: compare the buffer, actual capture rate and input gain, and check for overflow reports. If conversion sounds damaged, play that same converted file through Python too, so you don't confuse an ALSA playback-conversion problem with a recording problem. Using different files for the two playback tests doesn't isolate playback.

On Windows or macOS, the shared local test and echo controls above work the same way. You can also compare with a recording from the operating system's recorder; when comparing playback applications, use the same saved file in both. Actual audio quality still needs checking on the device you're using; automated capture tests don't establish microphone quality or platform-wide hardware compatibility.

The normal turn limit is 60 seconds. `--phrase-time-limit` changes it on the client; don't exceed the server's `phrase_time_limit` setting. If an existing PC configuration still has `"phrase_time_limit": 30`, change that to `60` in `local-config.json` and restart the server. An explicit `--phrase-time-limit 30` in an existing client launcher also needs updating. The client `--listen-timeout` controls how long it waits for speech to start (10 seconds by default). Device listing and the cue test stay on the client; an actual voice turn sends the recording to your configured Iris server.

## 5. Optional: local wake detection

For hands-free use, install the optional Vosk library on the client:

```sh
python -m pip install "vosk>=0.3.44,<0.4"
```

Download and unpack a small English model from the official
[Vosk model list](https://alphacephei.com/vosk/models), such as
`vosk-model-small-en-us-0.15`. Point Iris at the unpacked folder, not the ZIP or
its parent directory:

```sh
python iris.py client --server http://192.168.1.10:8765 --activation wake --wake-model ./vosk-model-small-en-us-0.15
```

Update both the server and client before using the conversation controls. Start the client with the same `--activation wake` command as above; no extra flag is needed to enable the conversation window.

Say “hey iris”, wait for the listening bloop, then give your message. After Iris finishes speaking, she quietly returns to listening, and you can give your next message without saying her name again.

Say **“stop listening”** as a complete utterance to close the conversation window. Iris says “Stopped listening” and the client goes back to waiting locally for “hey iris”. The PC handles the stop phrase before the chat model, outside the conversation history. Text mode, Enter mode and continuous mode don't treat it as a wake control.

Five minutes without a new detected utterance also returns the client to wake waiting, without sending that silence to the server or playing an error cue. Server processing and reply playback don't use up the idle interval; the timer starts afresh after each reply. Change the idle period with `--conversation-timeout SECONDS` on the client (default `300`, greater than 0, up to `3600`). `--listen-timeout` still applies to ordinary Enter/continuous turns; an open wake conversation waits for the remaining conversation interval instead.

Chat history is kept when you say “stop listening” or the conversation goes idle, so the next wake phrase can resume the discussion. Say `exit` to stop the client, or use Ctrl-C. With memory off, either gives you a fresh conversation on the next client run. With [conversation memory](memory-and-privacy.md#conversation-memory) enabled, the next run resumes the saved conversation.

Say **“clear context”** as a complete utterance to start afresh without stopping the client. With memory off, the server discards this client's conversation history, keeping Iris's configured personality and instructions. With memory enabled, it starts a new shared conversation for your configured owner. The earlier conversation stays in the archive. Once the context is cleared, Iris says “Context cleared”. In wake mode, the conversation stays open, so carry on after the acknowledgement without repeating the wake phrase. This command also works in Enter and continuous modes, or typed in text mode. Capitalisation and common surrounding punctuation don't matter. A longer sentence containing “clear context” is still an ordinary chat message.

You can change the wake phrase with `--wake-phrase`, using words the Vosk model knows. While waiting for that phrase, background audio stays on the client. **While the conversation window is open, detected speech is sent to the PC without requiring a wake phrase for each turn**, including the “stop listening” utterance. This uses constrained local recognition for wake detection and the configured PC recogniser for your messages; try it with your own accent, microphone, distance and room noise rather than expecting every room to behave alike.

### Cancel something Iris picked up by mistake

If you hear the processing bloops and realise Iris picked up something you
weren't saying to her, say **“cancel, cancel”**. The bloops stop and the client
says “Cancelled”. Iris drops that turn before it can become a reply or
part of the conversation she remembers.

Update the server and the client, then add `--voice-cancel` to your client
command. If you already use wake mode, it shares the model from `--wake-model`.
In Enter or continuous mode, supply the unpacked Vosk model folder with
`--wake-model` as well. The cancel phrase is recognised locally. The window
ends when the bloops stop for a reply or an action. If you use an online speech
recogniser or chat model, that service may already have received the original
utterance by then.

## 6. Companion mode

Sometimes I just want Iris to hang out and chat, rather than keep asking what she can help with. Say **“companion mode on”** as a complete utterance, or type it in text mode. This asks the model to play the part of a relaxed robot companion: follow a tangent, have a bit of a laugh, and let a reply just be a reply without always tacking another question onto the end. She can still help with practical things when you ask. How well she settles into that role depends on the model and your configured personality prompt.

While companion mode is on, Iris can start a conversation herself after a random stretch of quiet. Initially that's somewhere between 90 seconds and four minutes, measured from when she finishes speaking. She picks from a bank of ideas and asks the model to write something fresh, using the recent conversation for context. There are ordinary check-ins, but also terrible robot business ideas, suspiciously specific awards, useless superpowers and things a pigeon might put in a strongly worded letter. The idea is to get an occasional unexpected thought or a laugh, rather than hear the same recorded “how was your day?” every time.

If you leave an opener unanswered, she gives you more space. The next random interval doubles, then doubles again, up to eight times the original range: eventually 12–32 minutes between attempts with the defaults.

A successfully recognised turn and completed reply start a fresh interval and reset that backoff. Background noise that triggers a recording but produces no recognised words doesn't reset either, and neither does a failed connection. She finishes the current recording and server request before checking whether an opener is due. Recognition can still mistake background sounds for words, so microphone sensitivity and placement matter too.

This is an initial attempt at getting the balance right; living with it for a while will be the useful test. To change the base interval, add these options to your usual client command, for example:

```sh
python iris.py client --companion-idle-min 180 --companion-idle-max 600
```

That starts with three to ten minutes of quiet. The values are seconds and must satisfy `5 <= min <= max <= 3600`; the backoff multiplies that range. Text mode uses the same timing, so you can try the personality and openers with `python iris.py client --text` before getting the microphone involved. Automatic openers don't play the working sound first.

Say **“companion mode off”** to go back to ordinary assistant conversation and stop the spontaneous openers. **“Stop listening”** also switches companion mode off; in wake mode it returns Iris to waiting for her wake phrase. “Clear context” clears the conversation while keeping companion mode on. The mode belongs to the current client session and starts off when you launch a new client.

In voice companion mode, Iris listens for follow-up speech without another wake phrase or Enter press, and the ordinary wake conversation timeout is suspended. **Detected speech is sent to the PC throughout companion mode**, so turn it off when you've finished hanging out. The microphone still closes while she speaks; she can't hear interruptions or someone starting to talk while an opener is being generated. There's no camera or arrival detection involved yet: you're telling her when you'd like company. Update both the server and client before trying this feature.

## 7. Start the Pi client at boot

Raspberry Pi OS (including older releases called Raspbian) can run Iris as your normal user from your home directory. The [systemd user service](../systemd/iris-client.service) starts `run_client.sh` and restarts it after ten seconds whenever it exits, including when you say `exit`. Use `systemctl --user stop iris-client` when you actually want it to stay stopped.

Use your existing `run_client.sh` if you've already set up the connection key, audio devices and wake model there. Its exported environment variables are passed to the client as usual. The service doesn't read `.bashrc` or inherit an SSH terminal's environment, so the script needs to export the key itself and use the Python environment explicitly, either by activating it or by calling `.venv/bin/python`.

Keep the client in the foreground, with no trailing `&`, and use `exec` for the final command that launches Python. That lets systemd track the client and stop it cleanly. Wake and continuous activation work without a terminal; Enter-to-talk and text mode don't. The launcher is ignored by Git so your local settings stay local.

If you'd rather keep the connection key outside the script, the service also reads an optional `~/.config/iris/client.env` file. Use `IRIS_DROID_TOKEN=your-existing-shared-key` without `export`, and restrict its permissions with `chmod 600 ~/.config/iris/client.env`. You don't need this file when your script already exports the key.

Now install the service from the repository directory:

```sh
mkdir -p ~/.config/systemd/user
cp systemd/iris-client.service ~/.config/systemd/user/
nano ~/.config/systemd/user/iris-client.service
```

The supplied unit assumes the checkout is `~/iris-llm`. If yours is somewhere else, change **both** `WorkingDirectory` and `ExecStart` in that installed copy. `%h` means your home directory; for example, a checkout in `~/git/iris-llm` needs `%h/git/iris-llm` in both paths. Bash runs the launcher, so it doesn't need an executable bit.

Stop any copy of the client running in a terminal, then enable startup:

```sh
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now iris-client.service
systemctl --user status iris-client.service
```

[Lingering](https://freedesktop.org/software/systemd/man/latest/systemd-run.html) starts your user service manager at boot and keeps it running after logout. Only that setup command uses `sudo`; Iris itself runs as you. The client handles failed network turns without exiting, and the service restarts it if startup or audio fails. Failed turns aren't automatically resent, so speak again once the PC is available.

Check audio after a reboot without logging into the Pi's desktop too. Direct ALSA devices need permission to access `/dev/snd`; if your account only gets that access during a local desktop session, add it to the `audio` group with `sudo usermod -aG audio "$USER"`, then reboot. If you use a PipeWire or PulseAudio device instead, its user audio service needs to be available at boot too. Keep the device choices that worked in your normal-user tests.

To follow the logs, restart after changing the launcher or key, or stop the client:

```sh
journalctl --user -u iris-client.service -f
systemctl --user restart iris-client.service
systemctl --user stop iris-client.service
```

To stop it and remove automatic startup, run `systemctl --user disable --now iris-client.service`. A restart creates a fresh client session, so companion mode stays off until you enable it again. Conversation history resumes only if you've enabled conversation memory on the server.

### If Raspberry Pi OS only shows your service logs with sudo

If `journalctl --user` shows nothing, see whether the client logs are already
in the system journal. Run this from your normal Pi login:

```sh
sudo journalctl -b -n 30 "_SYSTEMD_USER_UNIT=iris-client.service" "_UID=$(id -u)"
```

If that shows the logs, you can keep using that command. To read them directly
with `journalctl --user`, journald needs persistent storage and separate
journals for each user. Some Raspberry Pi OS installs set `Storage=volatile`
in a drop-in named `40-rpi-volatile-storage.conf`. That setting keeps the logs
in RAM and can explain an empty `/var/log/journal`. Drop-ins override the main
config and are read in filename order, so a `10-persistent.conf` drop-in would
also be overridden by the Pi's `40-…` file. You can see the files and settings
in order with:

```sh
systemd-analyze cat-config systemd/journald.conf
```

To keep the logs on disk and give each user their own journal, create a
drop-in that sorts after the Pi's file:

```sh
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/90-persistent.conf >/dev/null <<'EOF'
[Journal]
Storage=persistent
SplitMode=uid
EOF
sudo systemctl restart systemd-journald
sudo journalctl --flush
```

This changes logging for the whole Pi. The value is `uid`, and
`systemctl --user daemon-reload` only reloads user units, so use the journald
restart above to apply the logging settings. To check that your account can
read a fresh entry without restarting Iris or waiting for the updater, run:

```sh
systemd-run --user --wait --unit=iris-journal-test /bin/echo "Iris journal test"
journalctl --user -b -u iris-journal-test.service -n 10
```

## Optional face display

If you have a screen on the Pi, Iris can show a face as she listens, thinks and
talks, then go back to a quiet black screen with a little snowfall.
[Pick a face and set up the display](face-display.md).

## Optional home automation

Iris can switch a Matter plug, read its meters and edit its weekly schedule.
The [home automation service](home-automation.md) can run on an always-on
Pi alongside the voice client, so schedules still run when the PC goes to sleep.

[Back to Iris](../README.md) · [All guides](index.md)
