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
listening again. In wake mode, “hey iris” opens a conversation: after each reply
you can speak again without repeating her name. Say “stop listening”, or leave
five minutes of silence, to return to waiting for the wake phrase.

## Choose where each half runs

Follow [How to set up the server](#how-to-set-up-the-server) on the PC that handles the models, then [How to set up the client](#how-to-set-up-the-client) on the laptop or Pi with the microphone and speaker. Each guide includes its own installation, connection key and startup steps.

You can run both on one computer. Follow both guides, use one checkout and Python environment, and keep the server and client in separate terminals. Installing the client packages adds microphone support to the server installation. Use `http://127.0.0.1:8765` for the client connection; there's no need to open a LAN listener. If you use uv, include all three extras as shown in the guides.

## How to set up the server

Do this on the computer that will run speech recognition and synthesis and connect to the chat model. It doesn't need a microphone or speaker. Use Python 3.11–3.13; I'd start with 3.12, since the newest Python isn't always the easiest route with model libraries.

### 1. Install the server

Install Git first if needed. On Debian-based Linux or Raspberry Pi OS, use `sudo apt update` followed by `sudo apt install git`; on Windows or macOS, use your usual Git installation. Then get the repository (shared commands for all platforms):

```sh
git clone https://github.com/dkjroot/iris-llm.git
cd iris-llm
```

If you've already got the repository, open a terminal in that checkout instead. There's no need to clone another copy. If its `.venv` already exists, activate it rather than creating it again.

#### Windows (PowerShell)

With Python 3.12 and Git installed, open PowerShell in the repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

If PowerShell won't activate the environment, use `.\.venv\Scripts\python.exe` in place of `python` below. You don't need to change your execution policy just to run Iris. In Command Prompt, you can activate it with `.venv\Scripts\activate.bat` instead.

#### macOS (Terminal)

With Git and Python 3.12 installed, run these commands in the repository:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

#### Linux (Bash)

On a Debian-based system with a supported `python3`, install the prerequisites, then create the environment in the repository:

```sh
sudo apt update
sudo apt install git python3-venv python3-dev build-essential libportaudio2
python3 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

Check `python3 --version` first. If your distribution ships a Python outside 3.11–3.13, install a supported interpreter and use its matching venv/development packages. Other distributions need their equivalents of the packages above.

#### Install the Python packages (all platforms)

With the environment active:

```sh
python -m pip install -e ".[whisper,kokoro]"
```

The server uses Whisper for recognition and Kokoro for its voice. Neither ordinary startup nor an import downloads model files; preparing those is a separate step below. The server doesn't need the client's `microphone` extra.

If you prefer uv, use `uv sync --locked --python 3.12 --extra whisper --extra kokoro` instead of the venv/pip steps. Run subsequent commands with `uv run --no-sync python` instead of `python`. You can pick pip or uv; you don't need to use both. If this environment also runs the client, include `--extra microphone` in the sync command so all three extras stay installed.

After updating an existing checkout, rerun your package installation or uv sync command to pick up new dependencies. If startup reports missing timezone data, run `python -m pip install tzdata` in the active Iris environment (shared command for Windows and Linux). Windows needs this package to look up names such as `Europe/London`; changing a valid timezone in your config won't fix a missing database.

### 2. Configure the server and chat connection

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

On the PC, run (shared commands for all platforms):

```sh
python iris.py configure
python iris.py --check
```

The configuration script asks for the model address and name, the recogniser,
the voice and speaking speed, and optional time and weather skills. If you don't know the model name yet, the
check command lists the IDs advertised by servers that support `/v1/models`.
Use the appropriate ID and rerun configuration if necessary. A server without
that listing route can still work; try the [client text test](#3-test-the-connection-with-text) once the server is running.

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

Online weather has its own `weather_enabled` switch, described below. You can
enable it while keeping your chat model and speech processing local.

Configuration goes into `local-config.json`, which stays out of Git. The full
server defaults are in `config_template.json`; environment values named
`IRIS_<SETTING>` override the file. Unknown settings are rejected. Earlier
configuration files have their desktop-only fields removed when loaded, and
old system/Google voice choices are switched to Kokoro. Run configuration again
to save the cleaned-up file.

#### Optional skills: the clock and local weather

Iris can let the model choose when to use a skill during a conversation. There
are no special phrases to memorise: try “What day is it?”, “Will I need a coat
tomorrow?” or “What does the weekend weather look like?”. She can still just
chat or answer questions from her own knowledge. These are model function calls,
so your model and model server need to support them.

The first two skills are `get_local_time` and `get_local_weather`. The clock reads
the Iris server's system clock in your configured timezone, including daylight
saving. Keep that computer's date and time synchronised. Weather comes from
[Open-Meteo](https://open-meteo.com/), with current modelled conditions and a
daily forecast for up to seven days, in Celsius, mph and mm. The results include
dates, rain probabilities and the resolved location, rather than relying on the
model's memory of the weather. Iris asks the model to credit Open-Meteo when
using its data. Open-Meteo's free API needs no key for this personal setup; see
its [usage plans](https://open-meteo.com/en/pricing) for other uses.

Run `python iris.py configure` on the server to enable weather and set your town,
country and timezone. The clock skill is on by default; online weather starts
off. For Stroud in Gloucestershire, these are the relevant `local-config.json`
fields. Merge them into your existing configuration rather than replacing it:

```json
{
    "tools_enabled": true,
    "local_timezone": "Europe/London",
    "weather_enabled": true,
    "weather_location": "Stroud",
    "weather_country": "GB"
}
```

These settings also accept the usual `IRIS_` environment overrides. Weather
requests send the configured town and country to Open-Meteo's geocoder, then
coordinates and forecast options to its weather API. Conversation text, audio
and the model API key aren't sent to the weather service. The resolved location
is reused for that conversation; each weather call fetches a fresh forecast.
The weather result is then sent to your configured chat model so Iris can put it
into words. If the service fails, the model receives an error and is told to say
it couldn't check, rather than make up a forecast.

For **Gemma 4 with KoboldCPP**, use a recent KoboldCPP build with **Use Jinja**
enabled (or add `--jinja` to its launch command), and use a model chat template
that supports Gemma's tool calls. KoboldCPP handles the model's native format
and returns structured `tool_calls` through `/v1/chat/completions`; Iris runs
the named skill and sends its result back for the final reply. See the
[KoboldCPP implementation](https://github.com/LostRuins/koboldcpp/blob/concedo/koboldcpp.py)
and [Gemma function-calling guide](https://ai.google.dev/gemma/docs/capabilities/text/function-calling-gemma4).
Support in the upstream software doesn't establish that every older build or
GGUF template will work, so try the text client before testing speech.

To see what is happening, temporarily set `"trace": true` on the server, restart
it, and run `python iris.py client --text`. Ask for the time and tomorrow's
weather, then have an ordinary chat. A real call prints `Skill get_local_time:
ok` or `Skill get_local_weather: ok`, with its elapsed time; the trace also shows
the requested tool and returned result. A plausible answer alone doesn't prove
that a skill ran. Trace prints conversation text too, so turn it off after
testing. If generation stops before a complete call or reply, check KoboldCPP's
console and consider raising Iris's `max_tokens` for your model's thinking and
answer. The default is 250 tokens per model request.

Tools are optional (`tool_choice` is `auto`), and a normal answer still needs
only one model request. A skill needs another model request to turn the result
into a reply. Iris allows up to four tool rounds and eight calls per user turn.
She keeps complete tool exchanges in history and removes whole turns when the
history limit is reached. “Clear context” removes those exchanges too. Set
`"tools_enabled": false` to return to plain chat requests with no tool definitions
if your model server doesn't support them.

#### Using another model host or adding an adapter

The skills and conversation loop don't depend on KoboldCPP. The default adapter
uses the OpenAI-compatible Chat Completions API, with `base_url`, `model` and
`IRIS_API_KEY` selecting your local host or online endpoint. Set `allow_remote`
to `true` for a model endpoint outside localhost. That endpoint still needs to
support the chat and tool-call fields Iris uses; we haven't tested other hosts
as part of this feature.

Jinja is a setting in the model host, rather than a template engine Iris runs.
Leave Iris's `chat_template_kwargs` empty to let the host use its defaults. If
your host accepts per-request template options, put them in that dictionary.
Iris sends them as `chat_template_kwargs` without interpreting them. Additional
non-secret request fields can go in `chat_request_options`. Both settings are
empty by default, so Iris doesn't send template options or assume a particular
host. They also accept JSON through `IRIS_CHAT_TEMPLATE_KWARGS` and
`IRIS_CHAT_REQUEST_OPTIONS`. Use only fields supported by your chosen endpoint;
Iris's named settings and conversation/tool fields can't be replaced through
the extra options. Trace output includes these options, so keep authentication
in `IRIS_API_KEY`.

If your endpoint uses a different API format, implement the small `ChatBackend`
interface in [backends.py](iris_chat_engines/backends.py). Its `complete(messages,
tools)` method receives the conversation and skill definitions, and returns one
assistant `Message`, containing text or tool calls. The adapter translates to
and from the provider's format and raises `ChatError` on failure. Preserve tool
IDs and any reasoning the provider needs on subsequent calls, and leave the
input messages alone. Wire your adapter into `make_backend`, or pass it directly
to `ChatEngine(..., backend=your_adapter)` when embedding Iris. The history,
tool execution, call limits and voice client can carry on using the same code.
Adding another skill is separate again: register its argument schema and
handler in [iris_tools](iris_tools/__init__.py).

#### Does my LLM need a key as well?

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

### 3. Download the speech models

Now download the server's speech models:

```sh
python iris.py --download-models
```

Whisper defaults to `base.en` on CPU with int8 computation. Kokoro uses its CPU
ONNX model and voice pack. Keep the internet available until setup finishes;
afterwards normal startup uses the cached files. Kokoro's fixed files are checked
for size and SHA-256 hash. Use `--download-voices` to prepare just Kokoro, or
`--list-voices` to see the voice suggestions without loading a model.

### 4. Set the client connection key

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

### 5. Start the server

With the server's environment active, its connection key set, and the chat model still running, choose the command for your setup. These commands are shared by all platforms.

For a laptop or Pi connecting over the LAN:

```sh
python iris.py server --host 0.0.0.0
```

Allow inbound TCP port 8765 in the PC firewall for your private LAN, ideally restricted to the client. You don't need to change the router for two machines on the same LAN.

If both halves run on this computer:

```sh
python iris.py server
```

This uses `127.0.0.1:8765` and doesn't need a LAN listener. Leave this terminal running while you follow [How to set up the client](#how-to-set-up-the-client) in a second terminal. Ctrl-C stops the server.

#### What opening the LAN listener means

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

## How to set up the client

Do this on the laptop or Raspberry Pi with the microphone and speaker, or in the same checkout as the server if you're using one computer. The client records your speech and plays replies; Whisper, Kokoro and the chat model run on the server. You don't need to run `configure` or download those models on a separate client.

Use Python 3.11–3.13 (I'd start with 3.12), Git, and a working microphone and speaker. For a Pi, use a 64-bit OS; it isn't being asked to load the PC's models.

### 1. Install the client

Install Git first if needed. On Debian-based Linux or Raspberry Pi OS, use `sudo apt update` followed by `sudo apt install git`; on Windows or macOS, use your usual Git installation. Then get the repository (shared commands for all platforms):

```sh
git clone https://github.com/dkjroot/iris-llm.git
cd iris-llm
```

If you've already got the repository, open a terminal in that checkout instead. There's no need to clone another copy. If its `.venv` already exists, activate it rather than creating it again.

#### Windows (PowerShell)

With Python 3.12 and Git installed, open PowerShell in the repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

If PowerShell won't activate the environment, use `.\.venv\Scripts\python.exe` in place of `python` below. You don't need to change your execution policy just to run Iris. In Command Prompt, you can activate it with `.venv\Scripts\activate.bat` instead.

#### macOS (Terminal)

With Git and Python 3.12 installed, install PortAudio with Homebrew before building the PyAudio microphone adapter:

```sh
brew install portaudio
python3.12 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

Allow microphone access for the terminal or application you're using to launch the client when macOS asks for it. If pip stops with `fatal error: 'portaudio.h' file not found`, run the Homebrew command above and retry the client package installation below in your activated environment. You don't need to recreate the environment or install the full Xcode application for that error; the compiler has already got far enough to look for the header.

#### Linux and Raspberry Pi OS (Bash)

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

#### Install the Python packages (all platforms)

With the environment active:

```sh
python -m pip install -e ".[microphone]"
```

If you're sharing the server's environment on one computer, this adds microphone support to the packages already installed for the server.

If you prefer uv, use `uv sync --locked --python 3.12 --extra microphone` instead of the venv/pip steps. Run subsequent commands with `uv run --no-sync python` instead of `python`. If this environment also runs the server, use `uv sync --locked --python 3.12 --extra microphone --extra whisper --extra kokoro` so all three extras stay installed. You can pick pip or uv; you don't need to use both.

### 2. Set the connection key

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

### 3. Test the connection with text

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

### 4. Set up listening and audio devices

The client records mono audio at the microphone's reported default sample rate, unless you override it with `--microphone-rate`, and converts it to 16 kHz for the server. For playback, it checks whether the speaker accepts the audio's rate; if necessary it converts the samples to the speaker's reported default rate, keeping the duration and pitch. A USB device running at 44.1 or 48 kHz doesn't need to support the speech models' rates directly. Wake recognition uses the microphone's actual rate too.

All the commands in this section run on the **client**, with its Python environment active. You don't need to run `configure` or download Whisper or Kokoro on the Pi; those belong on the PC server. Keep the same `IRIS_DROID_TOKEN` and server address that worked for your text test.

The client defaults to Enter-to-talk. Use `--activation continuous` to listen whenever it's your turn. These are client choices; changing the server config doesn't change when the microphone opens. `--text` skips all audio, so a working text conversation confirms the connection but doesn't test recording or playback.

#### Choose the microphone and speaker

These commands are shared by Windows PowerShell, macOS and Linux Bash, including Raspberry Pi OS:

```sh
python iris.py client --list-microphones
python -m sounddevice
```

The first command lists names and indices from SpeechRecognition/PyAudio for `--microphone`. The second lists sounddevice devices for `--speaker`; choose one with output channels (`out`). The first list can include playback-only devices too, so choose an entry for your microphone rather than assuming every listed device can record. The two libraries enumerate devices separately, so choose each index from its own list.

For example, if your microphone is index `2` in the first list and your speaker is index `7` in the second, run:

```sh
python iris.py client --server http://192.168.1.10:8765 --microphone 2 --speaker 7
```

Replace the address and both indices with yours. These numbers are examples, not defaults. `--speaker` selects the output for both the listening cue and Iris's spoken reply. Without these options, the libraries use their default input and output devices. Device choices are command-line options; they aren't saved by the server's configuration script. Include them each time you launch the client, including with continuous or wake activation.

#### Raspberry Pi: ALSA card 3, device 0

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

#### Check playback, then try a voice turn

To test the chosen speaker without contacting the server, this shared command plays Iris's packaged listening cue through the same playback function the client uses. Replace `7` with your sounddevice output index:

```sh
python -c "from importlib.resources import files; from iris_droid.client import play_wav; play_wav(files('iris_sounds').joinpath('bleep.wav').read_bytes(), device=7)"
```

Then launch the voice client with both device options and your PC's address. Press Enter, wait for the cue and `Speak now...`, then say a short sentence and pause. The client stops recording after silence, sends the recording to the PC, prints the transcript and reply, and plays the reply. Enter starts a turn; it isn't a key you hold while talking. Say `exit` or press Ctrl-C to finish.

On Linux, device enumeration can print ALSA `Unknown PCM` messages and warnings that JACK isn't running before showing the list. Those messages alone don't establish that your selected device failed; check the device list and the result of the playback or capture attempt. `python -m sounddevice` only lists devices; adding an index to that command doesn't select or test one.

If that doesn't work:

- **No listening cue:** try the playback check above. Check the output index, speaker volume and mute controls. On Raspberry Pi OS, `alsamixer -c 3` opens the mixer for card 3, if it has mixer controls. Check that `--no-sounds` is absent and look for a feedback warning; optional cue failure disables feedback without preventing capture.
- **Cue plays, but no speech is heard:** check the microphone index and input gain/mute, and speak after `Speak now...`. `Didn't hear speech. Try again.` means speech didn't start within the listening timeout. An HTTP `422` means the recording reached the PC but its recogniser couldn't understand it.
- **Invalid device, channel or sample-rate errors:** recheck both device lists. To inspect your output device, run `python -c "import sounddevice as sd; print(sd.query_devices(1))"`, replacing `1` with your speaker index. `default_samplerate` is the rate the client uses if the speaker rejects the cue or reply's original rate. If this still fails, check that the selected devices support mono capture/playback and that their reported default rates work. An ALSA test using `plughw:3,0` can apply format conversion that a direct `hw:3,0` entry doesn't provide. An appropriate `default` or `sysdefault` entry may help if it routes to your device. Changing `--listen-timeout` won't fix a format error. The [ALSA configuration guide](https://www.alsa-project.org/wiki/Asoundrc) explains hardware and conversion plugins.
- **Device busy:** stop any `arecord`, `aplay` or other audio test still using the hardware before starting Iris. Run the client as your normal user in the same environment as the successful tests, rather than trying `sudo` as an audio fix.
- **Transcript and reply print, but no spoken reply:** repeat the playback check with the same speaker index. A text-mode run never plays speech; make sure `--text` is absent. An HTTP `503` points to the PC's speech service instead.

#### Feedback sounds on the client

The client plays feedback through the device selected with `--speaker`, including when the server is on another computer. The server doesn't need a speaker. The cues tell you which part of a turn Iris has reached:

| Sound | Meaning |
| --- | --- |
| Subtle rising chirp | Ready for the next turn, including after a spoken reply. In Enter mode, press Enter. In wake mode, use the wake phrase to open a conversation; once it is open, the listening cue follows automatically. |
| Brief listening bloop | Start your sentence now. In wake mode this confirms the wake phrase was accepted. |
| Short higher chirp | Recording has finished; the microphone is closed. If recording echo is enabled, you'll hear your recording next. |
| Quick quadruple chirp | A command was accepted: “clear context” keeps the conversation open; “stop listening” returns to wake waiting. |
| Repeating low working pulses | Waiting for the server to transcribe, ask the model and generate speech. These stop before the spoken reply, or if the turn fails or you stop the client. |
| Three short low pulses | No speech was heard, or a turn/audio service failed. Check the terminal for the reason. |

In wake and continuous modes, the ready chirp follows microphone preparation. When starting a wake conversation, wait for that chirp before saying the wake phrase. During an open conversation, wait for the listening bloop before each sentence; you don't need to repeat the wake phrase. Feedback isn't played over an open microphone. Finishing a conversation with `exit` stops the client rather than announcing another turn.

Feedback is on by default. Add `--no-sounds` to your client start script to disable the cues and working sound; spoken replies, `--echo-recording` and local-test playback still work. Text mode is always silent. If a feedback asset or cue playback fails, the client prints a warning and continues without feedback; a failure to play the actual reply or diagnostic echo is still an audio error.

The cues are original generated WAV files, so they don't need FFmpeg, downloads or a remote sound service. See [the sound guide](iris_sounds/README.md) for the filenames, generator and replacement advice. The busy sound covers the whole server request; it doesn't report individual STT, chat or synthesis progress.

#### Spoken formatting and missing opening words

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

#### Start with a local microphone test

If speech recognition is poor, start by listening to the recording. Stop any other client or audio test using the microphone, activate the client environment, and run this shared command on Windows PowerShell, macOS or Linux Bash:

```sh
python iris.py client --test-microphone --microphone 2 --speaker 7
```

Replace `2` and `7` with indices from your own microphone and speaker lists. Keep quiet until the cue and `Speak now...`, then say a complete sentence with a natural pause in it. The client records one turn, prints the capture rate, buffer size, speech threshold and recorded duration, then plays it back locally. It uses the listening and recording-finished cues but no working sound because it makes no server request. It needs no server, connection key or downloaded speech model, and saves no file. This is a plain recording test: `--activation` and wake-model options don't make it wait for a wake phrase. Ctrl-C cancels it. `--text` can't be combined with this test.

Check that the beginning, middle and end are all present, that the speed sounds natural, and that your voice is clear rather than very quiet or distorted. The duration includes retained silence around the sentence, so it won't be an exact measure of speaking time. If the test fails to hear speech, check the selected input, hardware gain and the sensitivity controls below.

Once that sounds right, use [the recording echo](#hear-what-iris-recorded-before-sending-it) during a real conversation. The local test sends nothing; echo mode sends the recording after playback. A clean, complete echo with an inaccurate transcript points towards the server's recogniser, language or model settings. An incomplete or damaged echo means capture or playback still needs attention before changing Whisper.

#### Client recording controls

These options apply to the local microphone test and to normal voice turns, including continuous and wake activation. Keep them in your client start command or script; they aren't server settings. Try one change at a time and compare the same sentence with the local test.

| Option | Default | When to change it |
| --- | --- | --- |
| `--microphone-rate HZ` | The selected microphone's reported default rate | Override a misleading device default, or select a rate you've verified the microphone supports, such as `48000`. Allowed range: 8,000–192,000 Hz; being in that range doesn't mean your hardware supports it. |
| `--microphone-buffer FRAMES` | `8192` | Increase for input overflow; reduce for finer capture timing if the device remains reliable. Allowed range: 256–65,536 frames. |
| `--pause-threshold SECONDS` | `1.2` | Increase if a thinking pause ends your sentence. Allowed range: 0.5–10 seconds. |
| `--listen-timeout SECONDS` | `10` | Allow more time for speech to begin after the cue. Allowed range: greater than 0, up to 300 seconds. |
| `--phrase-time-limit SECONDS` | `30` | Allow longer messages. Allowed range: greater than 0, up to 300 seconds; the server's `phrase_time_limit` must allow the same length. |
| `--calibration-duration SECONDS` | `0.6`, extended if needed to read at least one input buffer | Sample background noise for longer before the cue. Stay quiet during calibration. Allowed range: 0.1–10 seconds; actual calibration is rounded to audio buffers. |
| `--energy-threshold LEVEL` | Calibrated from background noise; reused within an open wake conversation | Set speech sensitivity manually when automatic calibration doesn't suit the room. A value greater than 0, up to 32,768, skips calibration. Lower values detect quieter speech but can also detect noise; higher values require louder speech. This isn't a microphone gain or volume control. |

The recording rate is a hardware choice; the outgoing format stays mono, 16-bit PCM at 16 kHz so the server receives the same format from every client. Increasing the capture rate doesn't change the Whisper model. Wake recognition is told the actual capture rate. Input is currently mono; a device that only supports multichannel capture needs a suitable mono route from the operating system.

There is no fixed ALSA card number, device index, input rate or speech energy level chosen for a particular microphone. The default buffer is a general reliability/timing tradeoff, not a hardware-specific preset: 8,192 frames is about 186 ms at 44.1 kHz, 171 ms at 48 kHz, or 512 ms at 16 kHz. A large buffer at a low rate makes both calibration and speech-boundary decisions coarser. Leave the rate automatic unless testing gives you a reason to override it.

For example, to test a microphone known to support 48 kHz while allowing two-second pauses (shared command for all platforms):

```sh
python iris.py client --test-microphone --microphone 2 --speaker 7 --microphone-rate 48000 --pause-threshold 2
```

If you need manual sensitivity, first look at the speech threshold printed by the local test or `--echo-recording`, then try a lower or higher value relative to that. A threshold that works for one microphone and input-gain setting needn't work for another. The threshold stays fixed while you speak so sustained speech doesn't get mistaken for rising background noise.

#### Hear what Iris recorded before sending it

Add `--echo-recording` to your client start script to hear each recording before it goes to the server. This works with Enter, continuous and wake activation. For example (shared command for all platforms, using the earlier example device indices):

```sh
python iris.py client --server http://192.168.1.10:8765 --microphone 2 --speaker 7 --echo-recording
```

After you speak, the microphone closes, the client prints the recording's duration and plays the exact outgoing recording through your selected speaker. Once playback finishes, it sends that same recording automatically and waits for Iris's reply. Ctrl-C during the echo stops the client before sending that turn. A playback failure also stops it before sending. Nothing is saved to disk, and `--text` skips the echo.

If the echo contains only the first part of your sentence, the recording was cut short before the server received it. If the whole sentence sounds clear but the printed transcript is incomplete, investigate recognition on the server. This makes it easier to check the audio before changing models or microphone settings. Remove `--echo-recording` when you've finished checking; it adds playback time to every voice turn.

#### Recordings that stop halfway through a sentence

The client briefly calibrates to background noise before the listening cue, then keeps that threshold fixed for the turn. In wake mode, it reuses the threshold throughout the open conversation, saving another calibration and microphone setup between replies. It calibrates again when it returns to waiting for the wake phrase. If the room noise changes during a conversation, say “stop listening” and wake Iris again to refresh it. Enter and continuous modes still calibrate each turn. Keep quiet during calibration and speak after `Speak now...`. The threshold doesn't keep rising with your voice while you're talking.

By default, 1.2 seconds of audio below the calibrated threshold ends the recording. If you pause to think while speaking, add `--pause-threshold 2` to your existing client command to allow a longer gap. This option works with Enter, continuous and wake activation, on all platforms. Keep `--echo-recording` enabled while checking that your whole sentence is captured.

`--pause-threshold` accepts 0.5–10 seconds. A longer value also means a longer wait before Iris sends your finished message. `--listen-timeout` only controls waiting for speech to begin; increasing it won't prevent a sentence ending early. `--phrase-time-limit` is the overall turn cap, so check that too if recordings consistently stop at the same duration.

#### Fast or crackly microphone recordings

The client uses an 8,192-frame microphone buffer (about 186 ms at 44.1 kHz), giving capture more breathing room than the speech library's 1,024-frame default. It also checks for input overflow: if PortAudio reports that samples were lost, the client discards the current recording and stops with an error instead of sending damaged speech to the server. These checks apply while waiting for the wake phrase as well as while recording a message.

If you get `Microphone input overflow`, try adding `--microphone-buffer 16384` to your existing client command. This is a client option, shared across Windows, macOS and Linux; it doesn't change the server configuration. For example, using the device indices from the earlier example:

```sh
python iris.py client --server http://192.168.1.10:8765 --microphone 2 --speaker 7 --microphone-buffer 16384
```

The setting is in frames, not milliseconds; larger buffers make capture less responsive to speech starting and stopping. The allowed range is 256–65,536 frames. Check for other processes keeping the client CPU busy too. A larger buffer is something to try, not a guarantee that every source of crackling is an overflow.

If a native ALSA recording sounds clean but an Iris recording sounds shortened or crackly, compare both recordings through the same playback route. Check offline sample-rate conversion separately from microphone capture before changing the Whisper model. Keep any diagnostic recordings local; ordinary client operation doesn't save them.

#### Linux / Raspberry Pi OS: compare ALSA with Python capture

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

The normal turn limit is 30 seconds. `--phrase-time-limit` changes it on the client; don't exceed the server's `phrase_time_limit` setting. The client `--listen-timeout` controls how long it waits for speech to start (10 seconds by default). Device listing and the cue test stay on the client; an actual voice turn sends the recording to your configured Iris server.

### 5. Optional: local wake detection

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

Say “hey iris”, wait for the listening bloop, then give your message. After Iris replies, you'll hear the subtle ready chirp and listening cue, and can give your next message without saying her name again. She still finishes speaking before opening the microphone; this isn't simultaneous listening or interruption support.

Say **“stop listening”** as a complete utterance to close the conversation window. The client stays running and returns to waiting locally for “hey iris”. The PC recognises the stop phrase, handles it before the chat model and returns a control acknowledgement without generating speech. It doesn't add that phrase to chat history. Text mode, Enter mode and continuous mode don't treat it as a wake control.

Five minutes without a new detected utterance also returns the client to wake waiting, without sending that silence to the server or playing an error cue. Server processing and reply playback don't use up the idle interval; the timer starts afresh after each reply. Change the idle period with `--conversation-timeout SECONDS` on the client (default `300`, greater than 0, up to `3600`). `--listen-timeout` still applies to ordinary Enter/continuous turns; an open wake conversation waits for the remaining conversation interval instead.

Chat history is kept when you say “stop listening” or the conversation goes idle, so the next wake phrase can resume the discussion. Say `exit` to clear the session and stop the client, or use Ctrl-C to stop it. Starting a fresh client creates a fresh session.

Say **“clear context”** as a complete utterance to start afresh without stopping the client. The server discards this client's conversation history, keeping Iris's configured personality and instructions. It prints a confirmation without calling the chat model or generating a spoken reply. In wake mode, the conversation stays open, so wait for the next listening cue and carry on without repeating the wake phrase. This command also works in Enter and continuous modes, or typed in text mode. Capitalisation and common surrounding punctuation don't matter; a longer sentence containing “clear context” is still an ordinary chat message. Update the server to enable the command.

You can change the wake phrase with `--wake-phrase`, using words the Vosk model knows. While waiting for that phrase, background audio stays on the client. **While the conversation window is open, detected speech is sent to the PC without requiring a wake phrase for each turn**, including the “stop listening” utterance. This uses constrained local recognition for wake detection and the configured PC recogniser for your messages; try it with your own accent, microphone, distance and room noise rather than expecting every room to behave alike.

### 6. Companion mode

Sometimes I just want Iris to hang out and chat, rather than keep asking what she can help with. Say **“companion mode on”** as a complete utterance, or type it in text mode. This asks the model to play the part of a relaxed robot companion: follow a tangent, have a bit of a laugh, and let a reply just be a reply without always tacking another question onto the end. She can still help with practical things when you ask. How well she settles into that role depends on the model and your configured personality prompt.

While companion mode is on, Iris can start a conversation herself after a random stretch of quiet. Initially that's somewhere between 90 seconds and four minutes, measured from when she finishes speaking. She picks from a bank of ideas and asks the model to write something fresh, using the recent conversation for context. There are ordinary check-ins, but also terrible robot business ideas, suspiciously specific awards, useless superpowers and things a pigeon might put in a strongly worded letter. The idea is to get an occasional unexpected thought or a laugh, rather than hear the same recorded “how was your day?” every time.

If you leave an opener unanswered, she gives you more space. The next random interval doubles, then doubles again, up to eight times the original range: eventually 12–32 minutes between attempts with the defaults. Speaking to her resets that backoff. This is an initial attempt at getting the balance right; living with it for a while will be the useful test. To change the base interval, add these options to your usual client command, for example:

```sh
python iris.py client --companion-idle-min 180 --companion-idle-max 600
```

That starts with three to ten minutes of quiet. The values are seconds and must satisfy `5 <= min <= max <= 3600`; the backoff multiplies that range. Text mode uses the same timing, so you can try the personality and openers with `python iris.py client --text` before getting the microphone involved. Automatic openers don't play the working sound first.

Say **“companion mode off”** to go back to ordinary assistant conversation and stop the spontaneous openers. **“Stop listening”** also switches companion mode off; in wake mode it returns Iris to waiting for her wake phrase. “Clear context” clears the conversation while keeping companion mode on. The mode belongs to the current client session and starts off when you launch a new client.

In voice companion mode, Iris listens for follow-up speech without another wake phrase or Enter press, and the ordinary wake conversation timeout is suspended. **Detected speech is sent to the PC throughout companion mode**, so turn it off when you've finished hanging out. The microphone still closes while she speaks; she can't hear interruptions or someone starting to talk while an opener is being generated. There's no camera or arrival detection involved yet: you're telling her when you'd like company. Update both the server and client before trying this feature.

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
