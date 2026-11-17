# How to set up the server

Do this on the computer that will run speech recognition and synthesis and connect to the chat model. It doesn't need a microphone or speaker. Use Python 3.11–3.13; I'd start with 3.12, since the newest Python isn't always the easiest route with model libraries.

## 1. Install the server

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

With Git and Python 3.12 installed, run these commands in the repository:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install --upgrade pip
```

### Linux (Bash)

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

### Install the Python packages (all platforms)

With the environment active:

```sh
python -m pip install -e ".[whisper,kokoro]"
```

The server uses Whisper for recognition and Kokoro by default for its voice. You can also set up Chatterbox Turbo if you'd like Iris to speak using a voice prepared from a recording. Preparing models is a separate step below. The server doesn't need the client's `microphone` extra.

If you prefer uv, use `uv sync --locked --python 3.12 --extra whisper --extra kokoro` instead of the venv/pip steps. Run subsequent commands with `uv run --no-sync python` instead of `python`. You can pick pip or uv; you don't need to use both. If this environment also runs the client, include `--extra microphone` in the sync command so all three extras stay installed.

After updating an existing checkout, rerun your package installation or uv sync command to pick up new dependencies. If startup reports missing timezone data, run `python -m pip install tzdata` in the active Iris environment (shared command for Windows and Linux). Windows needs this package to look up names such as `Europe/London`; changing a valid timezone in your config won't fix a missing database.

## 2. Configure the server and chat connection

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

Choose `custom` for a local model server or another OpenAI-compatible endpoint.
The configuration script asks for the model address and name, the model host's
context window, the recogniser, the voice and speaking speed, optional time and
weather skills, and whether to save conversation memory locally. With memory enabled,
it also offers semantic recall, companion follow-ups and the read-only memory search
skill. The optional advanced section covers the other server JSON settings, including
reply/context budgets, speech model paths, recognition language, recording length
and diagnostics. Press Enter to keep each current value. API credentials stay in
`IRIS_API_KEY`; the server's listening address and port remain launch options
(`--host` and `--port`), and its client connection key stays in `IRIS_DROID_TOKEN`.
If you don't know the model name yet, the
check command lists the IDs advertised by servers that support `/v1/models`.
Use the appropriate ID and rerun configuration if necessary. A server without
that listing route can still work; try the [client text test](client-setup.md#3-test-the-connection-with-text) once the server is running.

By default, Iris is told to ask a follow-up question only when she needs more
information to respond. Once she's answered, she can let the reply stand. You
can edit `conversation_style_prompt` in advanced configuration, or enter `""`
to remove the guidance. Restart the server after changing it.

These are the normal choices for a local setup:

| Question | What I'd start with |
| --- | --- |
| Chat base URL | The address from your model server, including `/v1`. |
| Model name | Its advertised model ID, or the model you've pulled in Ollama. |
| Speech recognition | `whisper` is the local default. |
| Speech synthesis | Choose `kokoro`, `chatterbox` for Chatterbox Turbo, or `pocket` for CPU voice cloning and streaming. |
| Voice | Press Enter for Heart (`af_heart`), or try Emma (`bf_emma`) for British English. |
| Talking speed | Start with `1.0`, then change it once you've heard her. |
| Outbound remote services | Leave this off when the model runs on this PC and speech stays local. |

### Using OpenRouter

Choose `openrouter` when the setup asks for a chat connection. Iris fills in
`https://openrouter.ai/api/v1` for you. Copy the full model ID from the
[OpenRouter model list](https://openrouter.ai/models), including the part before
the slash, and enter the context window listed for that model. If you want Iris
to use skills like the clock, pick a model that supports tool calling. You can
also turn skills off during setup.

Accept outbound remote services when prompted so Iris can send your chat to
OpenRouter. You can still use local speech recognition and synthesis. Create an
[OpenRouter API key](https://openrouter.ai/keys) and set it as `IRIS_API_KEY` in
the environment you use to run the Iris server. Run `python iris.py --check`
from that environment to check the key and list the available model IDs.

### Speech and outbound connections

The chosen synthesizer runs on the server and sends audio back to the client.
The client owns the microphone and you choose how it listens when you launch it.

“Outbound remote services” controls whether the PC can send conversation or
speech to another machine or an online service. It doesn't need to be enabled
for a laptop or Pi to connect to Iris. With a local chat model and local speech,
leave it off.

Online weather and phone notifications have their own `weather_enabled` and
`notifications_enabled` switches, described below. You can enable either while
keeping your chat model and speech processing local.

Configuration goes into `local-config.json`, which stays out of Git. The full
server defaults are in `config_template.json`; environment values named
`IRIS_<SETTING>` override the file. Unknown settings are rejected. Earlier
configuration files have their desktop-only fields removed when loaded, and
old system/Google voice choices are switched to Kokoro. Run configuration again
to save the cleaned-up file.

Setup, diagnostics and server commands now use the same configuration-file
selection rules. Both configuration wizards also accept `--config`. See
[configuration, request limits and upgrade details](runtime-contracts.md)
if you run Iris from another directory, install a wheel, or update an existing
conversation archive.

### Optional skills: the clock, local weather and phone notifications

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

#### Push notifications with Pushover

Iris can send a push notification to your iPhone when you ask her to. For example,
try “Send a notification to my phone saying the washing is ready”. This uses
[Pushover](https://pushover.net/). Check [Pushover's pricing](https://pushover.net/pricing)
for the current trial, allowance and purchase details, and the price shown in your
local App Store.

You can test the sender from a MacBook without running the Iris server, speech
models or a client at home:

1. Install the Pushover app on your iPhone, sign in and allow notifications.
2. Sign in to [Pushover's dashboard](https://pushover.net/). Copy your user key,
   then create an application/API token called Iris. These are two different keys.
3. From your Iris checkout, with its Python environment active, run:

   ```sh
   python -m iris_tools.notify
   ```

The command asks for both keys with hidden input and sends one test notification.
It doesn't save them or change your Iris configuration. If you haven't installed
Iris in that environment yet, run `python -m pip install -e .` first. Neither the
phone nor the MacBook needs to be on your home network; both need internet access.
You can choose the text or target a registered device by name:

```sh
python -m iris_tools.notify --device iphone --message "Hello from the MacBook"
```

Use the device name shown in Pushover, which may differ from your iPhone's usual
name. Leaving it out sends to all your registered Pushover devices, unless you've
set `IRIS_PUSHOVER_DEVICE`. A result of `accepted` means Pushover queued the message;
check the iPhone to confirm it appeared. The command exits with an error if it
can't confirm acceptance. It doesn't retry automatically, because a connection
failure can happen after the message has already been accepted.

When you're ready to use it through Iris, run `python iris.py configure` on the
server and enable Pushover notifications. Set `IRIS_PUSHOVER_API_TOKEN` and
`IRIS_PUSHOVER_USER_KEY` in the environment of the process that starts Iris, then
restart the server. The standalone test command also accepts these environment
variables instead of prompting. Keep the keys out of chat and source files.
They are excluded from the JSON that the configuration wizard saves.

These are the relevant non-secret settings to merge into `local-config.json`:

```json
{
    "tools_enabled": true,
    "notifications_enabled": true,
    "pushover_device": "iphone"
}
```

Use an empty `pushover_device` to send to all devices. Notifications start off,
and the `send_notification` skill is available only when both switches are on.
The model chooses the message and optional title; your server configuration fixes
the recipient. Messages use normal priority and respect Pushover quiet hours.
Pushover receives the notification text, title and recipient details. The API
keys aren't included in model prompts or tool results. As with any model-chosen
skill, the wording and decision to call it depend on your model following Iris's
instructions to send only when asked.

The reusable sender lives in `iris_tools.notifications`: construct a
`NotificationSender(settings, client)` and call `send(NotificationArguments(...))`.
It also works without model tools enabled, so a future sensor rule can call it
directly. Each sender serialises requests and allows at most one attempt every
five seconds, including failed attempts. That cooldown is in memory and resets
when the sender is recreated; it isn't a persistent alert deduplication system.
Sensor monitoring, conditions, delayed reminders and repeated-alert policies
still need to be added before Iris can automatically warn about a door lock or
a kitchen attention sensor. Sending a notification now doesn't set up a monitor.

For **Gemma 4 with KoboldCPP**, use a recent KoboldCPP build with **Use Jinja**
and **Jinja for Tools** enabled (`--jinja` and `--jinja_tools` when using command
arguments), and use a model chat template that supports Gemma's tool calls.
Save these choices in the `.kcpps` file if you launch with `--config`.
[KoboldCPP's option reference](https://koboldcpp.net/docs/reference/flags/)
explains why ordinary Jinja and tool-template handling are separate.
KoboldCPP handles the model's native format
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
history limit is reached. “Clear context” removes those exchanges from the active
context too. With memory enabled, the archive keeps only the final text and tool
names; restored history never runs old tools or restores their raw results. Set
`"tools_enabled": false` to return to plain chat requests with no tool definitions
if your model server doesn't support them.

### Using another model host or adding an adapter

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
host. Iris leaves `chat_template_kwargs` out of requests to OpenRouter.
Both settings also accept JSON through `IRIS_CHAT_TEMPLATE_KWARGS` and
`IRIS_CHAT_REQUEST_OPTIONS`. Use only fields supported by your chosen endpoint;
Iris's named settings and conversation/tool fields can't be replaced through
the extra options. Trace output includes these options, so keep authentication
in `IRIS_API_KEY`.

If your endpoint uses a different API format, implement the small `ChatBackend`
interface in [backends.py](../iris_chat_engines/backends.py). Its `complete(messages,
tools)` method receives the conversation and skill definitions, and returns one
assistant `Message`, containing text or tool calls. The adapter translates to
and from the provider's format and raises `ChatError` on failure. Preserve tool
IDs and any reasoning the provider needs on subsequent calls, and leave the
input messages alone. Wire your adapter into `make_backend`, or pass it directly
to `ChatEngine(..., backend=your_adapter)` when embedding Iris. The history,
tool execution, call limits and voice client can carry on using the same code.
Adding another skill is separate again: register its argument schema and
handler in [iris_tools](../iris_tools/__init__.py).

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

## 3. Download the speech models

Now download the server's speech models:

```sh
python iris.py --download-models
```

Whisper defaults to `base.en` on CPU with int8 computation. Kokoro uses its CPU
ONNX model and voice pack. Keep the internet available until setup finishes;
afterwards normal startup uses the cached files. Kokoro's fixed files are checked
for size and SHA-256 hash. Use `--download-voices` to prepare just Kokoro, or
`--list-voices` to see the voice suggestions without loading a model.

### Optional voice cloning with Chatterbox Turbo

Chatterbox runs on the Iris server. I use a separate Python environment because
its NumPy requirement clashes with Kokoro's. Keep the regular Iris environment
active and use these commands to build the separate one alongside it. The GPU
commands are for NVIDIA cards with a driver that supports CUDA 12.6.

**Windows (PowerShell), from the Iris checkout**

```powershell
uv venv --python 3.11 .venv-chatterbox
$turbo = '.venv-chatterbox/Scripts/python.exe'
```

**Linux (Bash), from the Iris checkout**

```bash
uv venv --python 3.11 .venv-chatterbox
turbo=.venv-chatterbox/bin/python
```

**Both platforms, for NVIDIA GPU synthesis**

```sh
uv pip install --python "$turbo" --index https://download.pytorch.org/whl/cu126 "torch==2.6.0+cu126" "torchaudio==2.6.0+cu126"
uv pip install --python "$turbo" -r iris_tts_engines/chatterbox-requirements.txt
```

For CPU synthesis, use the same commands with the `/whl/cpu` index and
`torch==2.6.0+cpu` and `torchaudio==2.6.0+cpu`. Choose `cpu` in the Iris
configuration before preparing a voice.

In the **regular Iris environment**, choose `chatterbox` in
`python iris.py configure`, then prepare the model and voice:

```sh
python iris.py voice download
python iris.py voice prepare "path/to/your-recording.wav" my-iris-voice
```

Use a clean recording of about ten seconds. Turbo uses the recording to work
out how the voice sounds and saves those features in
`prepared-voices/my-iris-voice.pt`. This directory stays out of Git. Run
`python iris.py configure` again and select `file:my-iris-voice`, or choose
`bundled` to try Turbo's own voice. Restart the Iris server after switching.
It loads the model and prepared voice once, then keeps them ready between
replies. The recording and replies are processed locally.

If you already have the weights or a separate environment, set
`chatterbox_model_dir` or `chatterbox_python` in advanced settings. The
`--download-models` command prepares the speech recognizer and selected speech
synthesizer. `voice download` prepares Turbo on its own.

### Optional CPU voice cloning with Pocket TTS

Pocket runs on the server CPU, leaving the GPU free for the chat model. Use
a separate environment on the server. The Pi uses its usual Iris client
environment.

**Windows (PowerShell), from the Iris checkout**

```powershell
uv venv --python 3.11 .venv-pocket
$pocket = '.venv-pocket/Scripts/python.exe'
```

**Linux (Bash), from the Iris checkout**

```bash
uv venv --python 3.11 .venv-pocket
pocket=.venv-pocket/bin/python
```

**Both platforms, on the server**

```sh
uv pip install --python "$pocket" --index https://download.pytorch.org/whl/cpu "torch==2.8.0+cpu"
uv pip install --python "$pocket" -r iris_tts_engines/pocket-requirements.txt
```

Back in your **regular Iris environment**, run these on either platform:

```sh
python iris.py voice --engine pocket download
python iris.py voice --engine pocket prepare "path/to/your-recording.wav" my-iris-voice
python iris.py configure
```

Choose `pocket` and then `file:my-iris-voice`. A clean, single-speaker WAV
works best. The prepare command uses up to the first 30 seconds and saves the
voice as `prepared-voices/my-iris-voice.safetensors`. That directory stays out
of Git. Downloads use the English Pocket checkpoint from the listening test.
The recording is processed on the server, and the server reuses the prepared
voice between replies.

Restart the server and update the Pi's Iris client too. With Pocket selected,
the command-line client starts playback as audio arrives. Iris still waits for
the chat model's reply text before synthesis begins. Browser chat, older Iris
clients and the `--save-reply` diagnostic receive complete WAVs. If speech is
missing at the start on the Pi, try adding `--no-speech-stream` to the client
command. The client waits for the whole reply before playing it and keeps the
audio in memory. The option works on Windows and Linux. If your Pi runs Iris
as a service, add it to the Python command in `run_client.sh` and run
`systemctl --user restart iris-client`.
Use `--trace-voice` on the Pi to see when the first chunk was generated and
when the speaker was scheduled to play the first speech sample.

## 4. Set the client connection key

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

## 5. Start the server

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

This uses `127.0.0.1:8765` and doesn't need a LAN listener. Leave this terminal running while you follow [How to set up the client](client-setup.md#how-to-set-up-the-client) in a second terminal. Ctrl-C stops the server.

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

[Back to Iris](../README.md) · [All guides](index.md)
