# iris-llm

Iris is evolving! I started out wanting a Jarvis, with chat based home automation as my goal. For a while, Iris stood as a handy example of how to do voice-to-voice communication with an LLM all in python using local models, back when that was something that no one had really got working yet. There's still some useful code in here, and especially in the prototypes branch that I'll leave up. You could check the history too, but there are plenty of products now that support voice interaction with an LLM. So I'm going back to my original idea, but I don't just want a voice assistant that can turn my lights on and off, I want a droid! I've waited long enough, and if no one wants to fulfill my star wars kid fantasies without invading my privacy, well I'll just have to build one myself! So that's what Iris is now evolving into, the brain for a droid that I still need to build (and that will be able to turn my lights on and off, and hold a friendly conversation) 

**[Support my work on Buy Me a Coffee](https://buymeacoffee.com/dkjroot)**

### Free to use. Please help keep it that way.

I develop this project to be freely available to everyone, supported, if at all,
by voluntary contributions. I do not want other people to profit from my work
by selling copies, putting it behind a paywall, or building a paid application
or hosted service around it. I ask you to respect that intention: please keep
this work freely available, credit its origins, and direct people to
[my official GitHub repository](https://github.com/dkjroot/iris-llm) for the software.

If you want to help fund my development, please contribute **directly to me**
through [Buy Me a Coffee](https://buymeacoffee.com/dkjroot). Paying a distributor
or hosting provider does not fund my work unless I explicitly say otherwise.
There is no obligation to contribute.

**This is a personal request, not an additional license restriction.** The
[GNU General Public License v3.0](LICENSE) permits commercial use and
redistribution, including forks, subject to its terms. Those legal permissions
remain unchanged by this request. See [licensing details](LICENSING.md) and
[third-party credits](THIRD_PARTY_NOTICES.md).

## What Iris does now

I've always wanted a droid, just like Luke. We're starting with the talking part:
Iris listens, asks the language model for a reply, and answers in a proper voice.
The wheels, camera and lights will have to wait until I've built something to
put them in. She can't control the house or drive around yet.

There is one way to run Iris now, with a server and a client. The server runs on
the PC that has the model horsepower, and the client looks after the microphone
and speaker. That client can be a laptop while you're trying it out, then a
Raspberry Pi when you're ready to build the droid. You can also run both on the
same computer; it's exactly the same setup, using a local connection.

```text
Laptop or Pi                          PC
microphone -> recorded speech ------> Whisper -> chat model -> Kokoro
speaker + printed reply       <------ spoken reply + transcript + reply text
```

The client can wait for Enter, listen whenever it's your turn, or use a small
local recogniser to listen for “hey iris”. For now, she finishes speaking before
listening again. Wake detection currently needs the phrase before each turn;
a conversation window is something I'd like to work on once the basic setup is
getting regular use.

## 1. Install the bits each machine needs

Use Python 3.11–3.13. I'd start with 3.12; using the newest Python isn't always
the easiest route when audio and model libraries are involved. You'll also need
Git, a working microphone and speaker on the client, and a chat model running on
the PC. The PC doesn't need a microphone or speaker of its own.

Get the repository on each machine you'll be using:

```sh
git clone https://github.com/dkjroot/iris-llm.git
cd iris-llm
```

If you've already got it, use that checkout. There's no need to clone another copy.

### Windows

With Python 3.12 installed, open PowerShell in the repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

If PowerShell won't activate the environment, use
`.\.venv\Scripts\python.exe` in place of `python` below. You don't need to change
your execution policy just to run Iris. In Command Prompt, you can activate it
with `.venv\Scripts\activate.bat` instead.

### macOS

Use an installed Python 3.12. The client also needs PortAudio before pip can
build its PyAudio microphone adapter. With Homebrew, install it in Terminal:

```sh
brew install portaudio
```

Then create your Python environment:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

Allow microphone access for the terminal or application you're using to launch
the client when macOS asks for it.

If pip stops with `fatal error: 'portaudio.h' file not found`, PortAudio is the
missing piece. Run the Homebrew command above, then retry
`python -m pip install -e ".[microphone]"` in your activated environment. You
don't need to recreate the environment or install the full Xcode application
for that error; the compiler has already got far enough to look for the header.

### Linux and Raspberry Pi OS

Install the Python venv/development packages, a compiler and PortAudio. On a
Debian-based system whose `python3` is a supported version:

```sh
sudo apt update
sudo apt install git python3-venv python3-dev build-essential portaudio19-dev libportaudio2
python3 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

Check `python3 --version` first. If your distribution ships a Python outside
3.11–3.13, install a supported interpreter and use its matching venv/development
packages. Other distributions need their equivalents of the packages above.
For a Pi, use a 64-bit OS. A Pi 4 with 8 GB should have plenty of room for the
client's part; it isn't being asked to load the PC's models.

### Install the server or client packages

Once your environment is active, these commands are the same on all platforms.
On the **PC server**:

```sh
python -m pip install -e ".[whisper,kokoro]"
```

On the **laptop or Pi client**:

```sh
python -m pip install -e ".[microphone]"
```

If you're running both on one machine, install both sets together:

```sh
python -m pip install -e ".[microphone,whisper,kokoro]"
```

The server uses Whisper for recognition and Kokoro for its voice. The client
doesn't need either model. Neither ordinary startup nor an import downloads
model files. The first download is a separate setup step below.

If you prefer uv, the checked-in lockfile provides the reproducible route:
`uv sync --locked --extra whisper --extra kokoro` on the server, or
`uv sync --locked --extra microphone` on the client. Include all three extras
for one machine, and run subsequent commands with `uv run --no-sync python`
instead of `python`. Use a supported interpreter, for example with `--python 3.12`.
You can pick pip or uv; you don't need to use both.

## 2. Configure the PC server

Get your chat model working first. Iris doesn't load the chat model itself; it
uses an OpenAI-compatible chat endpoint. That describes the API format, rather
than requiring an OpenAI account or a cloud model.

Common local addresses are:

| Model server | Base URL |
| --- | --- |
| KoboldCPP | `http://127.0.0.1:5001/v1` |
| llama.cpp server | `http://127.0.0.1:8080/v1` |
| Ollama | `http://127.0.0.1:11434/v1` |

Use the address your model server actually reports, and check that it answers in
its own interface first. Keep it running in its own process while Iris runs.
The model endpoint can stay on localhost: the laptop or Pi connects to Iris,
which talks to the model on its behalf.

On the PC, run:

```sh
python iris.py configure
python iris.py --check
```

The configuration script asks for the model address and name, the recogniser,
and the voice and speaking speed. If you don't know the model name yet, the
check command lists the IDs advertised by servers that support `/v1/models`.
Use the appropriate ID and rerun configuration if necessary. A server without
that listing route can still work; try the client text test below.

These are the normal choices for a local setup:

| Question | What I'd start with |
| --- | --- |
| Chat base URL | The address from your model server, including `/v1`. |
| Model name | Its advertised model ID, or the model you've pulled in Ollama. |
| Speech recognition | `whisper` is the local default. |
| Voice | Press Enter for Heart (`af_heart`), or try Emma (`bf_emma`) for British English. |
| Talking speed | Start with `1.0`, then change it once you've heard her. |
| Outbound remote services | Leave this off when the model runs on this PC and speech stays local. |

Kokoro is selected automatically because it produces the audio that goes back
to the client. There is no server “start listening” setting: the client owns the
microphone and you choose how it listens when you launch it.

“Outbound remote services” controls whether the PC can send conversation or
speech to another machine or an online service. It doesn't need to be enabled
for a laptop or Pi to connect to Iris. With a local model, Whisper and Kokoro,
leave it off.

Configuration goes into `local-config.json`, which stays out of Git. The full
server defaults are in `config_template.json`; environment values named
`IRIS_<SETTING>` override the file. Unknown settings are rejected. Earlier
configuration files have their desktop-only fields removed when loaded, and
old system/Google voice choices are switched to Kokoro. Run configuration again
to save the cleaned-up file.

Now download the server's speech models:

```sh
python iris.py --download-models
```

Whisper defaults to `base.en` on CPU with int8 computation. Kokoro uses its CPU
ONNX model and voice pack. Keep the internet available until setup finishes;
afterwards normal startup uses the cached files. Kokoro's fixed files are checked
for size and SHA-256 hash. Use `--download-voices` to prepare just Kokoro, or
`--list-voices` to see the voice suggestions without loading a model.

## 3. Set the droid connection key

Iris needs a shared key so the client can connect to the server. Choose one and
set **the same value in both terminals**, whether those terminals are on one
computer or two. The variable is called `IRIS_DROID_TOKEN`.

You can use a short key if you want to. Iris will recommend a longer one but
will still start. To generate a random one, run this once and copy the result:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

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

Start the server or client from the terminal where you set the variable. These
commands apply to that terminal session and the programs launched from it;
they don't update an already-running program or carry over into a new terminal.
If you're using an IDE or a service, put the value in its launch environment.
Keep the key out of committed configuration files.

### Does my LLM need a key as well?

There are two connections here: **client → Iris server → model server**.
`IRIS_DROID_TOKEN` belongs to the first connection. Most local model servers
work without a key on the second, in which case there's nothing else to set.

If your model endpoint requires authentication, its key belongs in
`IRIS_API_KEY` on the **PC server only**. That value is sent to the model endpoint;
it isn't the key that lets the laptop or Pi connect to Iris. Use the key your
model endpoint expects. Iris doesn't impose a minimum length on it either.

```powershell
# Windows PowerShell, on the PC server only, if the model requires a key.
$env:IRIS_API_KEY = 'your-model-endpoint-key'
```

```bat
REM Windows Command Prompt, on the PC server only, if the model requires a key.
set "IRIS_API_KEY=your-model-endpoint-key"
```

```sh
# macOS, Linux or Raspberry Pi OS, on the server only, if the model requires a key.
export IRIS_API_KEY='your-model-endpoint-key'
```

This is an optional credential for your chosen model endpoint, rather than a
second key everybody needs to invent for Iris. If the endpoint is outside the
PC, you'll also need to enable outbound remote services in configuration.

## 4. Start Iris and try a conversation

### Both halves on one computer

In the first terminal, with your environment active and the droid key set:

```sh
python iris.py server
```

In a second terminal in the same repository, activate the environment, set the
same key, and try a text turn:

```sh
python iris.py client --text
```

This goes through the same Iris server as voice does; it just skips microphone
capture, transcription and synthesis for the turn. Type `exit` to finish.
Then run the voice client:

```sh
python iris.py client
```

Press Enter, wait for the short listening cue, then speak. The terminal shows
what Iris heard and her reply, and the client plays her voice. Say `exit` or
press Ctrl-C to stop the client. The server stays running until you stop it.

### A laptop or Pi talking to the PC

On the PC, allow Iris to listen for connections from another machine:

```sh
python iris.py server --host 0.0.0.0
```

On the laptop or Pi, use the PC's actual LAN address:

```sh
python iris.py client --server http://192.168.1.10:8765 --text
```

Replace `192.168.1.10` with your PC's address. Once the text test works, leave off
`--text` to use the microphone and speaker. The address is Iris's port `8765`,
not the model server's address or port. `0.0.0.0` is a listening address, not an
address to give the client; `127.0.0.1` always means the machine you're on.

Allow inbound TCP port 8765 in the PC firewall for your private LAN, ideally
restricted to the client. The client doesn't need an incoming firewall rule.
Both machines need to be able to reach each other; guest Wi-Fi sometimes keeps
devices apart even when they're connected to the same router.

### What opening the LAN listener means

By default Iris listens on `127.0.0.1`, so only this computer can connect.
`--host 0.0.0.0` listens on all of its IPv4 interfaces, including the LAN, subject
to the computer's firewall rules. That lets the laptop or Pi connect.

For a typical home network behind an IPv4 NAT router, this **doesn't by itself
make Iris accessible from the internet**. Incoming internet traffic would normally
need a port-forwarding rule, or another existing route into your network, to
reach it. You don't need to change the router for the PC and droid to talk on
the same LAN. [TP-Link's port-forwarding explanation](https://www.tp-link.com/us/support/faq/1379/)
shows how router forwarding makes a service inside the home reachable from outside.

I'm using this as a home-LAN setup, and I'm not making a claim about the safety
of everybody else's network. Your routing and firewall configuration may differ.
In particular, a public address, an existing port-forward/DMZ rule, a tunnel or
VPN can change who can reach the PC. Iris doesn't create router mappings or
public tunnels. Keep it on your trusted network and don't forward this port to
the internet. This small HTTP service doesn't encrypt the key or conversation;
if you need to cross an untrusted network, use an encrypted VPN or HTTPS proxy.

## Listening and audio devices

The client defaults to Enter-to-talk. Use `--activation continuous` to listen
whenever it's your turn. These are client choices; changing the server config
doesn't change when the microphone opens.

To list microphone indices on the client:

```sh
python iris.py client --list-microphones
```

Pass your chosen index with `--microphone N`. For output devices, run
`python -m sounddevice` and use `--speaker N`. The microphone and speaker lists
come from different audio APIs, so their indices don't necessarily match.

The normal turn limit is 30 seconds. `--phrase-time-limit` changes it on the
client; don't exceed the server's `phrase_time_limit` setting. The client
`--listen-timeout` controls how long it waits for speech to start.

### Local wake detection

For hands-free use, install the optional Vosk library on the client:

```sh
python -m pip install "vosk>=0.3.45,<0.4"
```

Download and unpack a small English model from the official
[Vosk model list](https://alphacephei.com/vosk/models), such as
`vosk-model-small-en-us-0.15`. Point Iris at the unpacked folder, not the ZIP or
its parent directory:

```sh
python iris.py client --server http://192.168.1.10:8765 --activation wake --wake-model ./vosk-model-small-en-us-0.15
```

Say “hey iris”, wait for the listening cue, then give your message. You can
change the phrase with `--wake-phrase`, using words the model knows. Background
audio stays on the client; it isn't sent to the PC while Iris waits for her name.
This uses constrained local speech recognition, so expect to try it with your
own accent, microphone, distance and room noise. It isn't a promise that every
room will give perfect wake detection.

## If something doesn't work

- If the connection fails, check the PC's address, the server's listening address,
  the private-network firewall rule and whether the model and Iris processes are
  both still running.
- An HTTP `401` means the client and Iris server have different droid keys.
- An HTTP `422` means the server didn't recognise speech. Try again after the cue.
- An HTTP `502` means Iris couldn't get a model reply. Check its address, model
  name and authentication, then try `python iris.py --check` on the PC.
- An HTTP `503` means the server's speech service failed. Check its console,
  installed extras and cached model files.
- If the microphone doesn't work, check permissions and the selected device.
  A Bluetooth speaker doesn't necessarily provide a working microphone too.
- If a model is missing, run `python iris.py --download-models` on the PC while
  online. Startup deliberately doesn't download missing files for you.

Iris doesn't automatically resend a failed network turn. The PC might have
finished it before the connection dropped, so resending could repeat a reply.
Restart the client for a fresh conversation if things seem out of step.

## Configuration, privacy and the code

Conversation history stays in the PC's memory, with the configured number of
turns and up to 16 recent client sessions. A fresh client run starts a fresh
session; saying `exit` clears that session. Restarting the server clears them
all. This code doesn't save recordings or chat history to disk. The server's
`trace` setting can print conversation text to its terminal if you enable it.

The local setup keeps model traffic on the PC and sends recordings and replies
between the client and PC. There is no automatic fallback to a hosted service.
A local model address can't tell us whether that model server itself forwards
data elsewhere, so check its configuration too. Optional `wav2vec2` recognition
needs its matching extra, while Google recognition requires outbound remote
services and sends audio online. Neither is needed for the normal Whisper setup.

`iris.py` provides the commands. `iris_droid/server.py` owns the speech models,
model connection and client sessions, while `iris_droid/client.py` owns recording
and playback. The recognition adapters live in `iris_stt_engines`, Kokoro in
`iris_tts_engines`, and chat requests in `iris_chat_engines/compatible.py`.
`iris_settings.py` checks server configuration before models are loaded.

For development, install `python -m pip install -e ".[dev]"`, then run:

```sh
ruff check .
ruff format --check .
mypy
pytest
```

The tests use fake devices and models, plus local HTTP tests. They don't need a
microphone, GPU, downloaded weights or a working model server. Actual listening
quality and wake accuracy still need a conversation on the hardware you'll use.
The [prototypes branch](https://github.com/dkjroot/iris-llm/tree/prototypes/examples)
keeps the earlier experiments if you're interested in how this started.
