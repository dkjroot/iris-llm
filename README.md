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

## Things I want Iris to do next

Alongside building her a body, I want Iris to help look after the house. A few
things on the list are:

- Send push notifications to my mobile phone, so she can let me know about alerts,
  alarms and the status of things around the house.
- Monitor the front door lock, so I can ask her whether it's locked.
- Control automatic roller blinds.
- Monitor temperature and humidity around the house.

I'd like a choice of personalities too, so I can try different characters and
conversation styles without rewriting Iris's prompt each time.

I'd also like her to help with things that need regular attention, especially in
the kitchen. If I put something on the hob or in the oven, I want to be able to
say, “Iris, don't let me leave this unattended for more than 40 minutes.” She'd
watch for me coming back to tend to it, and start that interval again when I do.
If she hasn't seen me tend to it within the time I've set, she'd alert me by
voice, push notification, or whichever alerting features I've enabled. The idea
could be useful for older people, but also for people like me who are easily
distracted. Filling a bath is another situation where that extra nudge could help.

Further down the line, I'd like to explore controlling things like the cooker
and filling the bath automatically, taking the home automation side of Iris much
further.

These are goals for future work; she can't do them yet.

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
that listing route can still work; try the [client text test](#3-test-the-connection-with-text) once the server is running.

The advanced `conversation_style_prompt` describes Iris as curious without
interviewing her friend: she can let a thought stand, with a conversational
follow-up roughly one reply in four. This is a light character nudge, not an
enforced quota; she can still ask for clarification. You can edit it in advanced
configuration, or enter `""` to remove it. Restart the server after changing it.

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

Online weather and phone notifications have their own `weather_enabled` and
`notifications_enabled` switches, described below. You can enable either while
keeping your chat model and speech processing local.

Configuration goes into `local-config.json`, which stays out of Git. The full
server defaults are in `config_template.json`; environment values named
`IRIS_<SETTING>` override the file. Unknown settings are rejected. Earlier
configuration files have their desktop-only fields removed when loaded, and
old system/Google voice choices are switched to Kokoro. Run configuration again
to save the cleaned-up file.

#### Optional skills: the clock, local weather and phone notifications

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

##### Push notifications with Pushover

Iris can send a push notification to your iPhone when you ask her to. For example,
try “Send a notification to my phone saying the washing is ready”. This uses
[Pushover](https://pushover.net/), which currently advertises a one-time US$4.99
purchase for iPhone/iPad, a 30-day trial and no subscription for individual use.
The included allowance is 10,000 messages a month across your Pushover account.
Check [Pushover's pricing](https://pushover.net/pricing) for the current details
and the price shown in your local App Store.

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
history limit is reached. “Clear context” removes those exchanges from the active
context too. With memory enabled, the archive keeps only the final text and tool
names; restored history never runs old tools or restores their raw results. Set
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

#### Control the speaker volume by voice

On the Pi, you can say “set volume to 50 percent”, “volume up”, “volume down”,
“what's the volume?”, “mute the speaker” or “unmute”. Up and down change the
volume by ten percentage points. You can also say “turn the volume down by
five percent”. Numbers work as words too, so “set volume to fifty percent”
does the same thing.

Enable this on the client with `--volume-backend alsa`. Install `alsa-utils`
if `amixer` isn't already available (`sudo apt install alsa-utils` on Raspberry
Pi OS). Choose the card and playback control that drive your speaker:

```sh
aplay -l
amixer -c 3 scontrols
amixer -M -c 3 sget 'PCM'
python iris.py client --server http://192.168.1.10:8765 --speaker 7 --volume-backend alsa --alsa-card 3 --alsa-control 'PCM'
```

Those card, speaker and control values are examples: use yours. The mixer
control might be called `Master`, `PCM`, `Speaker` or something else; use the
name shown by `amixer`. Without overrides, Iris uses the default ALSA card and
the `Master` control. `--alsa-card` also accepts an ALSA card ID. It is separate
from the sounddevice index used by `--speaker`, so make sure they refer to the
same output. Put these options in your client launcher if you run it as a
service. They belong on the Pi, rather than in the PC's server configuration.

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

If the mixer fails, Iris reports the failure rather than claiming success.
Check the `amixer` command above as the same user who runs the client. If the
connection fails after a change, the client prints the result it read locally
and doesn't resend the change automatically. You can ask for the current
volume once the connection is back.

Other platforms can add a backend by implementing `VolumeBackend.execute`
in `iris_droid/volume.py` and registering it in `make_volume_backend` and the
client's `--volume-backend` choices. The command recognition, transport and
spoken confirmation are shared across backends.

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
| Subtle rising chirp | Ready to start. In Enter mode, press Enter. In continuous mode it plays once at startup; in wake mode it announces waiting for the wake phrase. |
| Brief listening bloop | Start your sentence now. In wake mode this confirms the wake phrase was accepted. It doesn't repeat between turns in an open hands-free conversation. |
| Short higher chirp | Recording has finished in Enter mode or the local microphone test. Hands-free turns skip this cue. |
| Quick quadruple chirp | A command was accepted: “clear context” keeps the conversation open; “stop listening” returns to wake waiting. |
| Repeating low working pulses | Waiting for the server to transcribe, ask the model and generate speech. These stop before the spoken reply, or if the turn fails or you stop the client. |
| Three short low pulses | A turn/audio service failed, or an Enter-to-talk/local test heard no speech. Ordinary hands-free listening timeouts and unrecognised recordings don't play an error cue. |

When starting a wake conversation, wait for the ready chirp before saying the wake phrase, then wait for the listening bloop before your first sentence. Once the conversation is open, or companion mode is on, Iris quietly returns to listening after each reply. You don't need to wait for another cue or repeat the wake phrase. Continuous mode also listens quietly between turns. Feedback isn't played over an open microphone. Finishing a conversation with `exit` stops the client rather than announcing another turn.

Feedback is on by default. Add `--no-sounds` to your client start script to disable the cues and working sound; spoken replies, `--echo-recording` and local-test playback still work. Text mode is always silent. If a feedback asset or cue playback fails, the client prints a warning and continues without feedback; a failure to play the actual reply or diagnostic echo is still an audio error.

The cues are original generated WAV files, so they don't need FFmpeg, downloads or a remote sound service. See [the sound guide](iris_sounds/README.md) for the filenames, generator and replacement advice. The busy sound covers the whole server request; it doesn't report individual STT, chat or synthesis progress.

#### Reject kitchen noise with local speech detection

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

#### Hear what Iris recorded before sending it

Add `--echo-recording` to your client start script to hear each recording before it goes to the server. This works with Enter, continuous and wake activation. For example (shared command for all platforms, using the earlier example device indices):

```sh
python iris.py client --server http://192.168.1.10:8765 --microphone 2 --speaker 7 --echo-recording
```

After you speak, the microphone closes, the client prints the recording's duration and plays the exact outgoing recording through your selected speaker. Once playback finishes, it sends that same recording automatically and waits for Iris's reply. Ctrl-C during the echo stops the client before sending that turn. A playback failure also stops it before sending. Nothing is saved to disk, and `--text` skips the echo.

If the echo contains only the first part of your sentence, the recording was cut short before the server received it. If the whole sentence sounds clear but the printed transcript is incomplete, investigate recognition on the server. This makes it easier to check the audio before changing models or microphone settings. Remove `--echo-recording` when you've finished checking; it adds playback time to every voice turn.

#### Recordings that stop halfway through a sentence

Without `--vad-model`, the client briefly calibrates to background noise before capture, then keeps that threshold fixed for the turn. In wake mode, it reuses the threshold throughout the open conversation, saving another calibration and microphone setup between replies. It calibrates again when it returns to waiting for the wake phrase. If the room noise changes during a conversation, say “stop listening” and wake Iris again to refresh it. Enter and continuous modes still calibrate each turn. Keep quiet during calibration and speak after `Speak now...`. The threshold doesn't keep rising with your voice while you're talking.

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

The normal turn limit is 60 seconds. `--phrase-time-limit` changes it on the client; don't exceed the server's `phrase_time_limit` setting. If an existing PC configuration still has `"phrase_time_limit": 30`, change that to `60` in `local-config.json` and restart the server. An explicit `--phrase-time-limit 30` in an existing client launcher also needs updating. The client `--listen-timeout` controls how long it waits for speech to start (10 seconds by default). Device listing and the cue test stay on the client; an actual voice turn sends the recording to your configured Iris server.

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

Say “hey iris”, wait for the listening bloop, then give your message. After Iris replies, she quietly returns to listening, and you can give your next message without saying her name again. She still finishes speaking before opening the microphone; this isn't simultaneous listening or interruption support.

Say **“stop listening”** as a complete utterance to close the conversation window. The client stays running and returns to waiting locally for “hey iris”. The PC recognises the stop phrase, handles it before the chat model and returns a control acknowledgement without generating speech. It doesn't add that phrase to chat history. Text mode, Enter mode and continuous mode don't treat it as a wake control.

Five minutes without a new detected utterance also returns the client to wake waiting, without sending that silence to the server or playing an error cue. Server processing and reply playback don't use up the idle interval; the timer starts afresh after each reply. Change the idle period with `--conversation-timeout SECONDS` on the client (default `300`, greater than 0, up to `3600`). `--listen-timeout` still applies to ordinary Enter/continuous turns; an open wake conversation waits for the remaining conversation interval instead.

Chat history is kept when you say “stop listening” or the conversation goes idle, so the next wake phrase can resume the discussion. Say `exit` to stop the client, or use Ctrl-C. With memory off, either gives you a fresh conversation on the next client run. With [conversation memory](#conversation-memory) enabled, the next run resumes the saved conversation.

Say **“clear context”** as a complete utterance to start afresh without stopping the client. With memory off, the server discards this client's conversation history, keeping Iris's configured personality and instructions. With memory enabled, it starts a new shared conversation for your configured owner. The earlier conversation stays in the archive, but isn't included in the new context. It returns a text confirmation without generating a chat reply or speech; a pending memory summary checkpoint may still run. In wake mode, the conversation stays open, so carry on after the acknowledgement without repeating the wake phrase. This command also works in Enter and continuous modes, or typed in text mode. Capitalisation and common surrounding punctuation don't matter; a longer sentence containing “clear context” is still an ordinary chat message. Update the server to enable the command.

You can change the wake phrase with `--wake-phrase`, using words the Vosk model knows. While waiting for that phrase, background audio stays on the client. **While the conversation window is open, detected speech is sent to the PC without requiring a wake phrase for each turn**, including the “stop listening” utterance. This uses constrained local recognition for wake detection and the configured PC recogniser for your messages; try it with your own accent, microphone, distance and room noise rather than expecting every room to behave alike.

### 6. Companion mode

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

### 7. Start the Pi client at boot

Raspberry Pi OS (including older releases called Raspbian) can run Iris as your normal user from your home directory. The [systemd user service](systemd/iris-client.service) starts `run_client.sh` and restarts it after ten seconds whenever it exits, including when you say `exit`. Use `systemctl --user stop iris-client` when you actually want it to stay stopped.

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

## If something doesn't work

- If the connection fails, check the PC's address, the server's listening address,
  the private-network firewall rule and whether the model and Iris processes are
  both still running.
- An HTTP `401` means the client and Iris server have different droid keys.
- An HTTP `422` means the server didn't recognise speech. Try again after the cue.
- An HTTP `502` means Iris couldn't get a model reply. Check its address, model
  name and authentication, then try `python iris.py --check` on the PC.
- An HTTP `503` means speech or conversation storage failed. Check the returned
  error and the PC console. For speech, check installed extras and cached model
  files; for memory, check the database path, available disk space and access.
- If the microphone doesn't work, check permissions and the selected device.
  A Bluetooth speaker doesn't necessarily provide a working microphone too.
- If a model is missing, run `python iris.py --download-models` on the PC while
  online. Startup deliberately doesn't download missing files for you.

Iris doesn't automatically resend a failed network turn. The PC might have
finished it before the connection dropped, so resending could repeat a reply.
Say “clear context” for a fresh conversation if things seem out of step.

## Configuration, privacy and the code

Conversation memory is off by default. With it off, history stays in the PC's
RAM, with the configured number of turns and up to 16 recent client sessions.
A fresh client run starts a fresh conversation; saying `exit` clears that session,
and restarting the server clears them all. Turning memory on saves conversation
text in a local database, as described below. Iris never saves microphone recordings.
The server's `trace` setting can print conversation text to its terminal if you enable it.

### Conversation memory

If we've spent an evening talking, I'd quite like Iris to remember it when I turn
her back on. Run `python iris.py configure` on the PC and answer yes to saving
conversation memory. You can choose the database file and an owner name there,
or set these fields in `local-config.json`:

```json
{
    "memory_enabled": true,
    "memory_database": null,
    "memory_owner": "default",
    "memory_context_chars": 24000,
    "model_context_tokens": 8192,
    "context_margin_tokens": 512,
    "memory_summary_tokens": 1200,
    "memory_summary_interval": 8,
    "memory_summary_chars": 3000,
    "memory_retrieval_chars": 3000
}
```

These are additions to your existing configuration. A null database path uses
`%LOCALAPPDATA%/iris-llm/memory.sqlite3` on Windows,
`~/Library/Application Support/iris-llm/memory.sqlite3` on macOS, and
`$XDG_DATA_HOME/iris-llm/memory.sqlite3` on Linux (or
`~/.local/share/iris-llm/memory.sqlite3` if that variable isn't set). A custom path
can be absolute or relative to the server's working directory. Environment overrides
are `IRIS_MEMORY_ENABLED`, `IRIS_MEMORY_DATABASE`, `IRIS_MEMORY_OWNER` and
`IRIS_MEMORY_CONTEXT_CHARS`. Restart the server after changing these settings.

Iris resumes one active conversation per owner. The Pi and laptop
share that conversation, regardless of their temporary connection IDs. This is a
single-user setup: an owner name selects local data on the server; it isn't a login
or a way to give different people private accounts. If you're running several
servers, give each its own database. Don't open the same archive from two running
servers at once.

Iris saves each completed exchange after the reply and any speech have passed
validation. Stopping the client, restarting either machine, or evicting an old
RAM session doesn't remove the archive. “Stop listening” and companion mode off
keep the conversation available. A new client still starts with companion mode
off. “Clear context” starts a new conversation for all your clients; it doesn't
erase the old one. Just changing the subject continues the current conversation.
You can temporarily stop reading and saving conversation memory with the spoken
private-mode controls below. Setting `memory_enabled` off and restarting the
server still disables persistence altogether.

On a restart, Iris loads recent complete exchanges, up to `history_turns` and
`memory_context_chars` characters in total. She also has source-linked notes from
the active conversation and a few matching excerpts from the archive. An ordinary
question triggers a local keyword search using your words and recent conversation;
you don't have to ask for a memory tool. The excerpts include their original dates
and exchange IDs. This is keyword matching, so a distinctive name or subject helps.
Source-linked summary notes can help find a discussion even when their wording
differs from the original exchange. Optional semantic recall can also find
paraphrases with few shared words; its setup is below.

“Clear context” also blocks automatic recall of earlier conversations. Asking
explicitly to remember or resume a subject can search them again. That gives us a
fresh start without silently deleting the archive. `history_turns: 0` disables
history, summaries and retrieved excerpts in the chat prompt while still archiving
new exchanges if memory is enabled.

#### Private conversations and forgetting

Sometimes I want a chat without adding it to Iris's saved history. Say **“private
mode on”** or **“start a private conversation”** as a complete utterance. Iris speaks
a confirmation. Switching into private mode clears the working context on every
client for your configured owner and starts with fresh RAM history. While private, she neither reads saved
conversations nor saves the new exchanges, summaries or embeddings. She can still
chat and use the enabled live skills.

Say **“private mode off”** or **“end the private conversation”** to leave it. Iris
discards private working history across those clients and resumes the saved
conversation from before private mode. Private exchanges aren't copied back into
it. **“Memory status”** tells you whether private mode and conversation saving are
on. These controls work by voice or typed through the text client; capitalisation
and surrounding punctuation don't matter.

Private mode applies to all clients sharing the configured owner. With persistence
enabled, the mode flag survives client and server restarts, `exit`, and eviction of
a RAM session, until you turn it off. The database stores that flag and a change
counter, without private conversation text. Private RAM history itself is bounded
by the usual history/context limits and is lost on clear, restart or eviction.
“Stop listening” and companion mode off keep the private flag on. “Clear context”
while private discards that client's private RAM context and leaves saved records
alone. If persistence is disabled in configuration, no database is created; the
private diagnostic settings then last only for the running server process.

Update both server and client code to use this feature. The server disables Iris's
request/tool tracing during private chat. An updated client receives the private
state on each successful response, hides the transcript from normal terminal and
service-log output, and skips `--save-reply` files while private. The reply still
plays aloud. Older clients can connect, but don't know to hide private transcripts.
No new client configuration is needed for the default behaviour.

For an interactive text conversation where you deliberately want private replies
visible, launch the client with `--text --show-private-text`. That opts into terminal
output, which your terminal or service may retain; it still doesn't enable reply
WAV saving. Typed text can also remain visible in your terminal's input display.
Private mode controls Iris's own memory and these diagnostics. The configured chat
and speech services still receive the live requests, so their own logging settings
matter too. Existing terminal logs, exports, recordings made outside Iris and
backups aren't erased by entering private mode.

To delete the **current saved conversation**, say **“forget this conversation”**.
Iris tells you how many exchanges it contains, then waits for **“confirm forget
this conversation”**. Say **“cancel forgetting”** to cancel. A bare yes doesn't
delete anything. The request expires after two minutes and is cancelled by another
recognised utterance, a context/mode change or restart. If another client adds an
exchange before you confirm, ask again so you can confirm the updated scope.

Confirmed deletion removes that conversation's source exchanges, summaries, notes,
search chunks, embeddings and queued summary work, then starts a fresh saved
conversation if the active one was removed. It also clears cached context and recall
choices across your clients, so old RAM state can't silently save it again. When a
later conversation used a retrieved source, Iris records that connection. Forgetting
the source also removes those dependent conversations, including their other topics;
the confirmation tells you how many before you agree. Independent conversations stay
in the archive. These links start with this version: older, untracked paraphrases
aren't automatically found and erased. Explicitly remembered details are separate
and stay until you forget them. Independent backups, exports and old terminal/service logs keep their
own copies. “Clear context” continues to start afresh while retaining the archive;
it doesn't mean forgetting.

If you've just recalled an older discussion, Iris distinguishes it from the
current saved conversation before accepting a deletion request. **“Forget the
current conversation”** makes that scope explicit and still needs the confirmation.
After selecting an older discussion, **“forget that conversation”** or **“forget the
recalled conversation”** targets its saved conversation instead. Iris identifies that
scope and asks for the same confirmation. The deletion unit is the whole saved
conversation, even if your recall referred to only one day's topic. You can also use
`memory list` and `memory delete --conversation ID` on the server. **“Forget this conversation” while private**
just discards the current client's private RAM conversation; it cannot delete the
saved conversation from before private mode.

#### Remembering and changing a detail

For something I deliberately want Iris to keep, I can give it a short label. These
are explicit, human-authored details, separate from the summaries she makes herself.
For example, say **“remember story captain as fictional Captain Violet”**, then
**“confirm memory change”** after she reads it back. That confirmation is useful
when speech recognition has misheard a name. **“Cancel memory change”** cancels it;
a bare yes doesn't confirm. Requests expire after two minutes, and another utterance,
context change or restart cancels the pending change.

**“List remembered details”** reads up to eight at a time. **“Show the detail about
story captain”** reads that label's current value. Labels match exactly, ignoring
capitalisation. Say **“correct story captain to fictional Captain Emerald”** to
replace its value, or **“forget the detail about story captain”** to remove it. Both
read back the proposed change and wait for **“confirm memory change”**. Immediately
after showing or saving one detail, **“forget that detail”** and **“correct that detail
to …”** can use the selected label. An ordinary chat turn clears that selection.

There is no fixed limit on how many explicit details you can save. Each labelled
entry can have a label up to 80 characters and a value up to 500. Iris searches a
bounded selection and fits only a small amount into her context; saving more doesn't
mean every reply will include every detail. Include
“fictional” in the value when it describes a character rather than you. Private
mode can't read or change these details. No client options are needed.

Corrections and deletions remove the old stored value. They also remove whole saved
conversations that received it as reference, including any other topics in those
conversations. Iris states that count before you confirm; cancel if you'd rather
export them first. This deliberately broad deletion keeps old answers, summaries
and later recorded recalls from restoring the old value. The controls themselves
aren't archived as chat exchanges. A label doesn't find every independently spoken
copy of the same fact elsewhere: to remove an older discussion, forget its
conversation, or use the full archive deletion below. Backups and exports keep
their own copies.

Use these controls when you want a durable change. An ordinary model reply saying
“I'll remember” or “I've forgotten” cannot make that change on its own.

#### Which memories come to mind

With conversation memory enabled, `memory_hierarchy_enabled` defaults to `true`.
Iris makes a small, separate metadata request before an ordinary chat reply, using
the configured model. It looks for clear human assertions, corrections and the
topics you're actually engaging with. The reply model receives compact reference
data, rather than a set of rules about scoring memories.

Confirmed lasting identity facts, such as your name, get priority in a small part
of the context, along with high-importance lasting preferences. Ongoing projects
and dated events are selected for relevance. An old bus journey stays in the archive
but doesn't occupy that core space simply because it was once discussed. Explicitly
saved name labels are also prioritised. This isn't unlimited context: candidate
searches and the final prompt remain bounded, even as the archive grows.

A natural correction such as “Actually, my name is David, not Dave” can supersede
the old interpretation immediately. The original exchanges and the older fact
version remain available for historical questions; they aren't rewritten or deleted.
If it clearly corrects a labelled detail, its current displayed value follows the
correction too. This is different from the explicit **“correct LABEL to VALUE”**
control above, which still reads back and confirms its broader deletion scope.
An uncertain or unresolved correction leaves the confirmed value alone. Nothing
about a model's own reply or a search result can correct a fact by itself.

Topic recency follows your attention. Returning to a story or developing it further
refreshes its activity. Iris merely mentioning it, offering an unanswered opener,
or retrieving it doesn't. “I finished that story ages ago, let's talk about The
Terminator” closes the known story without refreshing its engagement, then directs
attention towards the film. Discussing a completed project doesn't automatically
make it unfinished again. Original source timestamps stay separate from the last
engagement timestamp; “ages ago” doesn't acquire an invented completion date.

The interpretation is model-assisted and can make mistakes, including mistaking
fiction or a bad transcription for a real assertion. Source checks, uncertainty
labels and correction controls help, but don't make extraction infallible. Existing
archives remain searchable; startup doesn't reprocess your entire history. New
annotations build up as you talk. Private turns, automatic openers and failed turns
don't add these facts or engagement signals. Confirmed forgetting removes the
associated derived records and recorded dependencies too.

This adds one serial model request to ordinary human chat turns, so it can add
noticeable latency on a slower host. It uses `memory_summary_tokens` as its output
allowance. You can switch it off in advanced `python iris.py configure`, or set
`memory_hierarchy_enabled` to `false` and restart. That stops new interpretation and
hierarchy prompt selection without deleting the stored annotations or undoing
accepted corrections to labelled details. Normal conversation memory and explicit detail controls still work. No client settings
or additional model download are needed.

To inspect the current bounded candidates, their source evidence, validity and
last engagement, run on the server:

```sh
python iris.py memory inspect --query "Sapphire story"
```

The preview shows selected current facts; it isn't a transcript of the exact last
model prompt. `memory export` includes all stored fact versions, topics and attention
records alongside the original conversations. As with other maintenance commands,
stop the server first when inspecting a consistent snapshot of its archive.

#### Asking about an earlier discussion

You can say “remember the Sapphire story?”, “what were we talking about yesterday?”
or “recall the garden project last week”. Iris searches your saved summary notes
and original exchanges, then uses a few source excerpts to continue. This all runs
on the server and uses the existing memory settings; the client needs no new options.

Spoken dates use `local_timezone`, including daylight-saving changes and days near
midnight. “Yesterday” means the previous local calendar day. “Last night” currently
uses that same whole-day window. “Last week” means the previous Monday-to-Sunday
week, and “last month” means the previous calendar month. “Two days ago” and similar
numbered dates work too. “The other day” searches the previous fourteen days rather
than assuming a particular date.

If several discussions fit, Iris offers up to three dated choices. Answer with
“the first one”, “the second one”, or a subject such as “the story”. After a selection,
“No, the other one” can switch between two choices. A weak match asks for confirmation;
a bare yes only selects a single offered choice. If she finds nothing, she asks for
another subject, name or date, and you can give that clue in your next turn.

A vague “remember that thing?” can borrow the subject from your last two human
messages. If there is no useful subject or date, Iris asks which discussion you mean.
She groups possible discussions by saved conversation and local day; several topics
within the same conversation on the same day may still need a more specific question.

Choices stay available in that client's server session for ten minutes. A new topic,
“never mind”, clearing context, a client/server restart, or eviction of the RAM session
ends that clarification. If you restart while choosing, repeat the recall request.
The original records are checked again when you choose, so deleting or superseding a
source makes an old choice unavailable. Selecting an older discussion supplies
reference excerpts to the current conversation; it doesn't move the archive's active
conversation back to the old one.

If a reply needs more evidence than the automatic excerpts provide, Iris can also
use a small, read-only `search_memory` skill. It searches within the configured owner,
returns dated source excerpts, and can't change memory. It allows two searches per
reply within the usual overall tool limits. Multiple matches still need a discussion
choice. Set `memory_search_tool` to `false` to disable it; `tools_enabled: false`,
private mode, or disabling prompt history/retrieval also removes it. Automatic
companion openers never receive tool definitions.

#### Optional companion follow-ups

Iris can occasionally pick up an unfinished creative or practical topic in companion
mode. Enable **memory-based companion follow-ups** in `python iris.py configure`,
or set **`memory_companion_followups: true` on the server**. The default is off. It needs saved memory and the usual companion mode. The client
needs no extra configuration, and its listening timers and unanswered-opener backoff
work as before.

On each idle opener, there is a one-in-four chance to look for an eligible topic;
otherwise she uses the playful random seeds. The topic must have a current,
source-linked summary marked suitable for a follow-up, with human evidence from
the last fourteen days. Closed, superseded, cleared and uncertain topics aren't
eligible. Personal or sensitive subjects are excluded through both the summary
instructions and a conservative source-text filter. That classification can still
make mistakes, so leave the option off if unsolicited recollection isn't welcome.
She is instructed to invite a return to the topic without assuming you've made
progress since last time.

After a successful follow-up, the server waits at least six hours before another
memory follow-up, and at least a day before using the same conversation. Those
cooldowns survive restarts. Say **“don't bring that up again”** to exclude the last
memory follow-up's whole saved conversation from future companion choices. It
still exists for explicit recall. The last offered topic remains selectable for
that opt-out for a day after a restart. If memory search or derived data fails,
she falls back to a generic opener.

#### Optional semantic recall

The keyword search is useful when I remember a name, but sometimes all I remember
is the idea. “Water collection arrangement” ought to find the discussion about a
rainwater tank. You can enable a small local embedding model to help with that.
It runs on the PC server alongside Iris and leaves the chat model as it is.

With the server stopped, install the extra in its Python environment:

```sh
python -m pip install -e ".[memory]"
```

Run `python iris.py configure` and enable local semantic recall, or add these fields
to your existing PC configuration:

```json
{
    "memory_enabled": true,
    "memory_semantic_enabled": true,
    "memory_embedding_model_dir": null,
    "memory_semantic_min_score": 0.45
}
```

Then explicitly prepare the model while online:

```sh
python iris.py memory download-model
```

This downloads the pinned, quantised
[all-MiniLM-L6-v2 ONNX export](https://huggingface.co/Xenova/all-MiniLM-L6-v2/tree/751bff37182d3f1213fa05d7196b954e230abad9)
and tokenizer from Hugging Face, about 24 MB in total, and verifies both checksums.
The model and tokenizer go in `models/all-MiniLM-L6-v2` next to your database when
the directory setting is null. You can choose another server directory with
`memory_embedding_model_dir`; `download-model --directory PATH` overrides it for
that preparation command. Ordinary startup and inference only use the verified
local files. Missing files or extras produce a setup error; startup never fetches
the model for you. The client needs no extra package or configuration.

If you already have an archive, build its vectors before restarting the server:

```sh
python iris.py memory index
```

This resumes from chunks that haven't been indexed for this exact model version.
If this is your first use of memory, you can skip that command: there is no old
archive to index yet. While the server runs, searches index at most eight pending
chunks per turn, then search the vectors available so far. The first few searches
can therefore miss material in a large backlog until indexing catches up. For a
large existing archive, running `index` with the server stopped is the quicker way
to get it ready. Ctrl-C during offline indexing preserves finished batches, so the
same command can resume later. `memory index --reset` rebuilds the current owner's
vectors for the selected model while keeping source history and summaries.

The local model encodes short passages into 384-number vectors, using the
[authors' mean pooling and normalisation method](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2).
It uses two CPU inference threads and at most 256 word-piece tokens per passage.
Vectors are stored in SQLite with their model revision and encoding version,
roughly 1.5 KB per chunk before database overhead. Different model versions aren't
mixed. Deleting source conversations or rebuilding their derived chunks also
removes their vectors. Index writes recheck the source after inference, so stale
work cannot bring back a deleted exchange.

Retrieval combines word overlap, semantic similarity and recency. Explicit date
filters and the clear-context boundary still apply before choosing sources. A
match found only through semantic similarity in an explicit recall request asks
for confirmation, even if there is only one candidate. `memory_semantic_min_score`
filters weak similarities; it isn't a confidence percentage, and a matching score doesn't prove two discussions
are the same. English paraphrases are the intended use. Names, unusual wording
and long passages can still need another clue. If inference fails during a search,
Iris logs the failure type and keeps the keyword matches available.

To compare both methods on the bundled synthetic evaluation set, run:

```sh
python iris.py memory evaluate --corpus tests/fixtures/semantic_recall_cases.json
```

It creates a temporary test archive and reports model load, indexing and query
timings plus the retrieved evidence for each case. It doesn't read or modify your
real conversations. The fixture includes paraphrases, ambiguity, unrelated queries
and an example this model still misses. It is a small regression check, rather
than a promise of accuracy on every conversation. Evaluation uses prepared local
files, and accepts `--directory PATH` if you keep the model somewhere else.

Environment overrides are `IRIS_MEMORY_SEMANTIC_ENABLED`,
`IRIS_MEMORY_EMBEDDING_MODEL_DIR` and `IRIS_MEMORY_SEMANTIC_MIN_SCORE`. Turn semantic
recall off and restart the server to return to keyword/summary retrieval; the
source archive remains available. Back up the database before its automatic schema
upgrade, and stop the server before offline indexing or maintenance.

#### Context size and compaction

Set `model_context_tokens` to the context size actually enabled on your model
host, rather than the largest size advertised for the model. The default is 8192;
Iris doesn't discover that limit from the server. The configuration wizard asks
for it, and `IRIS_MODEL_CONTEXT_TOKENS` overrides it. This setting covers the whole
request, including instructions, history, tool definitions and the reply.

Iris reserves `max_tokens` for the reply and `context_margin_tokens` for template
overhead. Until the backend can supply its own tokenizer, she estimates input
size conservatively from UTF-8 bytes and message framing, including tool schemas,
reasoning and tool exchanges. This deliberately compacts earlier than an exact
token count usually would. It isn't a measurement of the model's native tokens;
leave a margin for your host's template.

As the estimated input approaches three quarters of its budget, Iris makes a
summary checkpoint. When the combined prompt is under pressure, she compacts the
older exchanges into notes and keeps up to two recent complete exchanges verbatim.
The usual `history_turns` limit is another checkpoint trigger. Every model request,
including tool follow-ups and automatic openers, gets a final size check. Whole
older turns can be removed to fit; a current message or tool exchange that still
won't fit produces an error rather than sending an oversized request.

The full conversation stays in SQLite even after it leaves the working context.
Summary notes distinguish topics, user statements, Iris's proposals, decisions and
open questions, with fiction and tentative details labelled. A summary reads a
bounded batch of original exchanges, rather than repeatedly rewriting one old
summary. Recent notes fit within `memory_summary_chars`; older source details can
come back through search. This extends useful continuity beyond one context window,
though it doesn't mean every detail of an evening is always in the prompt.

#### Summary checkpoints and maintenance

With memory enabled, Iris normally summarises after eight unsummarised exchanges,
or sooner under context pressure. Saying “stop listening”, switching companion
mode off, clearing context or exiting also requests a checkpoint. There is no
extra client notification when its local wake window times out; pending work can
continue on the next conversation turn.

Summaries use the configured chat model with no tools, a separate
`memory_summary_tokens` output allowance, and at most twelve original exchanges
per request. Each turn or control checkpoint processes at most one batch. The
persistent queue holds at most 64 conversations. Summary calls run serially with
chat, so a checkpoint adds model latency; it doesn't generate simultaneously in
the background. Enable `trace` temporarily to see their timings, remembering that
trace also prints conversation text.

Malformed summaries leave the last valid notes intact. Failed jobs wait at least
a minute between attempts and pause after three failures. Original exchanges remain
available, and normal chat falls back to recent text and whatever reference data
still works. If a single source exchange is too large, the summary gets labelled
beginning-and-end excerpts; notes from that partial view stay tentative. The full
source is still stored and searchable. Corrections that the summariser identifies
supersede older notes and exclude their source excerpts from normal search. This
is model-assisted bookkeeping, so inspect the export if a recalled detail seems
wrong; it isn't a guarantee that the model recognises every contradiction.

The database upgrades existing archives automatically when the enabled server
starts. Back it up first. Keyword search needs SQLite FTS5; if your Python build
lacks it, startup explains what is missing. The normal Python distributions usually
include it. The optional semantic model has its own explicit preparation command,
described above.

The archive contains recognised or typed input, final replies, UTC timestamps,
source IDs and the names of any tools used. Automatic companion openers are marked
as automatic events, rather than something you said. It doesn't contain audio,
model reasoning, configured API keys, or raw tool arguments and results. Anything
you actually say or type can still appear in the transcript, including personal
details. The database is an ordinary local file, without application encryption.
Treat it and its exports like your other personal documents. Restored tool-based
answers describe an earlier exchange; they aren't fresh time or weather readings.

If saving fails, the server reports an error and rolls back the working turn.
It doesn't quietly continue while pretending the exchange was saved. A dropped
connection can still happen after a successful save, and Iris doesn't automatically
retry requests. Storage IDs prevent the same exchange object being inserted twice;
they don't deduplicate network requests or guarantee tools run only once.

To inspect or remove an archive, run these on the PC with the server stopped:

```sh
python iris.py memory list
python iris.py memory export > iris-memory-export.json
python iris.py memory retry
python iris.py memory rebuild
python iris.py memory delete --conversation CONVERSATION_ID
python iris.py memory delete --all
```

The export includes versioned summaries, current note statuses, embedding model
identifiers/indexed-chunk counts, the owner's private-mode flag/change counter,
explicit remembered details, versioned hierarchy facts and topic attention,
their conversation-use links, companion exclusions
and cooldowns, and pending summary jobs alongside the source exchanges. `retry` resets failed summary jobs after you've
fixed the model or configuration. `rebuild` discards derived summaries and search
chunks for this owner, rebuilds the keyword index and queues fresh summaries from
the originals. Any semantic vectors for those chunks are removed too; they can be
rebuilt with `memory index` or gradually during later searches. It keeps source history and clear-context boundaries. Those new
summaries are generated on later server turns, so recall may temporarily rely on
raw excerpts. Run the enabled server once to upgrade a phase-one archive before
using the new maintenance commands.

Use the ID from `list` in the selected deletion command. If it has recorded dependent
conversations, the CLI stops and explains the extra scope; review the export and add
`--include-dependent` to delete those whole conversations too. `--all` removes every
conversation and explicit remembered detail for the configured owner, which is handy
for clearing a test archive. It leaves the private-mode flag as it was.
For a different configuration file, put `--config PATH` after `memory` and before
`list`, `export`, `inspect`, `retry`, `rebuild` or `delete`. Inspection also works with memory
disabled and never creates a missing archive. Deletion removes source exchanges
and their summaries, notes, search chunks and queued jobs from the live database;
independent exports and backups keep their own copies. Start the server again after
maintenance so it can't retain deleted material in RAM.

The full conversation archive has no automatic expiry or maximum turn count.
Context limits control how much Iris reads for a reply, not how much history she
keeps. Summaries and search indexes help her find things; they don't replace or
trim the original completed exchanges. That leaves the source history available
for better retrieval and models in future. Private conversations still aren't
saved, and confirmed forgetting still deletes its stated scope.

To back up the archive, stop the server and copy the
database file somewhere safe. Restore that copy to the configured path with the
server stopped. Keep backups out of your source checkout, and delete test exports
when you're finished with them.

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
