# Give Iris a remote voice

Run the server on your PC and the client on a laptop first. Later, run exactly
the same client on the Raspberry Pi 4 (8 GB). The PC keeps the speech and chat
models; the client needs audio drivers and, optionally, a small local Vosk model.
The Pi hardware and wake accuracy still need a real-device test.

```text
Laptop / Pi                           PC
local wake -> record one turn --WAV--> Whisper -> existing chat model
speaker + printed reply       <------ Kokoro WAV + transcript + reply text
```

This is the first, turn-based voice version. Say the wake phrase, pause for the
bleep, then say your message. Iris finishes her reply before listening again.
Each turn needs a new wake phrase in wake mode. There is no camera upload,
motor control, streaming speech, echo cancellation or interruption yet.

## 1. Prepare the PC

Use Python 3.11–3.13 (3.12 is a good starting point), the repository, and its
normal environment setup from the main README. Install the server extras:

```sh
python -m pip install -e ".[whisper,kokoro]"
```

Keep your existing KoboldCPP/llama.cpp process running on the PC. The model
endpoint can stay bound to localhost: the droid connects to Iris's new server,
which in turn talks to the model. The droid does not connect directly to port
5001 or need the model API key.

Use `python configure.py` to select Whisper and Kokoro, or edit your existing
`local-config.json` to include:

```json
{
  "stt_engine": "whisper",
  "tts_engine": "kokoro",
  "kokoro_voice": "bf_emma"
}
```

Keep your existing `base_url` and `model` fields. Then prepare the local files:

```sh
python iris.py --download-models
python iris.py --check
```

The server synthesizes Kokoro audio in memory and never opens the PC microphone
or speaker. Other TTS adapters currently play locally and cannot serve this mode.
The normal `python iris.py` desktop mode remains available.

Generate a shared secret, then copy it to both machines' environment variables:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

On the PC, in PowerShell:

```powershell
$env:IRIS_DROID_TOKEN = 'paste-your-generated-secret-here'
python -m iris_droid.server --host 0.0.0.0 --port 8765
```

Or in Bash/zsh:

```sh
export IRIS_DROID_TOKEN='paste-your-generated-secret-here'
python -m iris_droid.server --host 0.0.0.0 --port 8765
```

Allow inbound TCP port 8765 on the PC's private-network firewall, restricted to
your LAN/client. Use the PC's actual LAN address below, for example
`192.168.1.10`. `0.0.0.0` is a listening address, not a client destination;
`127.0.0.1` on the laptop would mean the laptop itself.

The default server bind is loopback; `--host` explicitly exposes it to the LAN.
This small server uses HTTP with a shared bearer secret: audio, replies and the
secret are not encrypted. Use it on a trusted private LAN only. For untrusted
networks use an encrypted VPN or HTTPS reverse proxy; do not port-forward this
service to the internet. `allow_remote` still controls the PC's outbound model
and speech services; it need not be enabled just to accept the droid connection.

## 2. Test from the laptop

Check out this same revision on the laptop and create an environment using the
main README. For a text-only test, install the base package:

```sh
python -m pip install -e .
```

Set `IRIS_DROID_TOKEN` in this terminal too, using the syntax above for your shell.
First check a complete chat turn without audio devices:

```sh
python -m iris_droid.client --server http://192.168.1.10:8765 --text
```

The text route skips both transcription and synthesis. Type `exit` to finish.
Then install microphone support and try the full speech path:

```sh
python -m pip install -e ".[microphone]"
python -m iris_droid.client --server http://192.168.1.10:8765
```

Press Enter, wait for the bleep, then speak. The terminal prints both the PC's
transcript and Iris's reply; the laptop speaker plays the returned audio.
Use `--activation continuous` to skip Enter. Say `exit` or press Ctrl-C to stop
the client. Stopping a client leaves the PC service running.

`python iris.py --list-microphones` lists SpeechRecognition microphone indices;
pass the desired index using `--microphone N`. For output indices use
`python -m sounddevice`, then `--speaker N`. Input and output indices come from
different audio APIs, so do not assume they match. macOS/Windows microphone
permissions and the normal Linux PortAudio setup still apply.

## 3. Add local wake detection

The optional wake adapter uses Vosk's streaming small-vocabulary recogniser.
It is separate from the original desktop `activation=wake` implementation and
does not load Whisper on the client. Idle audio stays on the client, even if
the PC is offline. Nothing downloads during startup.

Install the optional client library:

```sh
python -m pip install "vosk>=0.3.45,<0.4"
```

Download and unpack a small English model from the official
[Vosk model list](https://alphacephei.com/vosk/models), such as
`vosk-model-small-en-us-0.15`. Vosk describes its small models as suitable for
Raspberry Pi, using roughly 300 MB of memory. Use the unpacked model directory,
not the ZIP file or its parent folder:

```sh
python -m iris_droid.client --server http://192.168.1.10:8765 --activation wake --wake-model ./vosk-model-small-en-us-0.15
```

Say “hey iris”, wait for the bleep, then give the instruction. `--wake-phrase`
can select another phrase using words in the model vocabulary. This is a
constrained speech recogniser rather than a purpose-trained wake-word model;
false wakes and missed phrases are possible. Test your accent, distance and room
noise before relying on it. No Pi wake latency or accuracy has been measured here.

## 4. Move the client to the Pi

Your Pi 4 with 8 GB is the intended first hardware target. Use 64-bit Raspberry Pi
OS and a supported Python 3.11–3.13 interpreter. Check `python3 --version` first;
if the OS ships another version, install a supported interpreter and use it to
create the environment. The client does not need Whisper, Kokoro, PyTorch or a GPU.

On Raspberry Pi OS, the basic audio/build prerequisites are:

```sh
sudo apt update
sudo apt install git python3-venv python3-dev build-essential portaudio19-dev libportaudio2
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[microphone]"
```

Run those environment commands from this repository on the Pi. Use the matching
venv/development packages if you selected a different Python interpreter. Add
Vosk and the small model as above when ready for wake detection. The base Iris
package still includes the desktop audio libraries, but the droid client never
initialises the desktop TTS engines or loads the PC's models.

Connect a USB microphone/speakerphone and a working speaker, use reliable power,
set the shared secret, then run the same client command as on the laptop. Start
with Ethernet if convenient and switch to Wi-Fi once it works. Keep the PC server
and model process running. The Pi does not keep a local conversational brain when
the network is unavailable.

## Behaviour and troubleshooting

- `401`: the shared secrets differ or are missing.
- Connection refused/timeout: check the PC's address, bind address, firewall and
  whether both machines can reach each other (guest Wi-Fi can isolate devices).
- `422`: no speech was recognised; try again after the cue.
- `502`: the PC could not get a model reply; check its model process/configuration.
- `503`: the PC's speech service failed; check its console and local model files.
- Recording rejected: the client limit must not exceed the PC's configured
  `phrase_time_limit` (30 seconds by default); input is mono 16-bit 16 kHz PCM WAV.

Requests and replies are capped at 10 MB. Models process one turn at a time; this
is a single-droid prototype, not a concurrent inference service. Conversation
history is isolated by a random session ID for each client run, held only in PC
memory, and bounded to 16 recent sessions plus the normal `history_turns` cap.
Restarting the client starts fresh; saying `exit` clears its server session.
Restarting the server clears all sessions. Recordings are not saved by this code.
The normal PC `trace` option can still print conversations if enabled.

Failed network turns are never resent automatically: a disconnected response
may have already advanced PC history. Start a fresh client if its context seems
out of step. Close a failed client with Ctrl-C while fixing connectivity.

The transport lives in `iris_droid`, independently of local audio capture and
the existing model adapters. Camera data and body commands can gain their own
versioned messages later; no camera or actuator capability is implied by this
voice protocol.
