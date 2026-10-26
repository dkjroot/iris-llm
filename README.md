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

## What's changed?

I've given IRIS a bit of a tidy-up for late 2026. The old GPT completion calls and Oobabooga-specific chat API have been replaced with an OpenAI-compatible chat endpoint, so you can point it at KoboldCPP, llama.cpp, or another server that speaks the same API. There's a Whisper speech recogniser now, proper installation instructions, checked configuration, and some tests around the things that have caught people out. The beeps use local WAV files, so you don't need FFmpeg just to get past startup, and there's a compatibility option for those replies with funny escaped apostrophes. It's still a discussion bot, though. It won't turn your lights on!

## About IRIS

Thanks to the folks who reported the apostrophe and setup problems, by the way. Those were useful pointers to where this needed some attention.

The original idea was "IRIS: Intelligent Residential Integration System - a mind for your home!" I still want a Jarvis... but I never got into the home automation side of things. What I did get working was a voice conversation with a local model, and that turned out to be quite a useful little project on its own.

This is a personal project, but maybe there's something here that'll help you. The idea is that you can understand each piece, swap it for something you prefer, and see how it all fits together:

```text
microphone -> speech recognition -> text -> local LLM -> text -> speech -> speaker
```

If you're here to learn, the [prototypes branch](https://github.com/dkjroot/iris-llm/tree/prototypes/examples) has the step-by-step experiments. They build up from individual speech tests to a complete conversation. I've left those historical examples alone in this refresh; their old API calls and dependencies haven't all been updated. Start here for the maintained application, and use the experiments to follow how it grew.

## Getting it running

Want Iris to speak through a droid? The new [droid voice setup](docs/droid.md)
splits microphone/speaker capture onto a laptop or Raspberry Pi and keeps speech
recognition, the chat model connection and Kokoro synthesis on your PC. Start
with laptop-to-PC testing, then move the same client to a Pi. Optional Vosk wake
detection runs locally on the client. This first version supports turn-based
voice and text; camera input and body control are future work.

I'd start with Python 3.12 and a terminal in this repository. The package currently targets Python 3.11-3.13. Don't pick Python 3.14 just because it's the newest one; some of the audio/model dependencies may not be ready for it. A supported version in the manifest isn't a promise that every microphone, GPU and optional library has been tested on it.

You'll need [Git](https://git-scm.com/downloads) and Python, or uv's managed Python option below. If you haven't already got the repository, these commands work in both PowerShell and a Linux terminal:

```sh
git clone https://github.com/dkjroot/iris-llm.git
cd iris-llm
```

If you're already here, don't clone another copy. Start with the environment setup. If Linux doesn't have Git yet, run the distribution prerequisite commands below before cloning.

**Windows (PowerShell, with Python 3.12 installed):** check `py -3.12 --version` first. Python's [Windows downloads/install manager](https://www.python.org/downloads/windows/) or the uv route below can supply Python if you need it.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m ensurepip --upgrade
python -m pip install --upgrade pip
python -m pip install -e ".[microphone,whisper]"
```

If PowerShell blocks the activation script, you can skip activation entirely: use `.\.venv\Scripts\python.exe` wherever the instructions say `python`, including the two pip commands. There's no need to change your machine's execution policy. Windows uses its installed speech voices; no separate TTS model download is needed for the default voice.

**Linux (Bash):** the commands below use Ubuntu 24.04's Python 3.12 packages. Other distributions need their equivalents for Python/venv/development headers, a C compiler, PortAudio and eSpeak. On a distribution with a different default Python, check its version or use uv to supply 3.12.

```bash
sudo apt update
sudo apt install git python3.12 python3.12-venv python3.12-dev build-essential portaudio19-dev libportaudio2 espeak-ng libespeak1
python3.12 -m venv .venv
source .venv/bin/activate
python -m ensurepip --upgrade
python -m pip install --upgrade pip
python -m pip install -e ".[microphone,whisper]"
```

Run IRIS from a desktop session with a working microphone and speaker. A headless Linux server or WSL session needs its own audio setup; use text mode first in that case. On macOS, use an installed supported Python, `python3.12 -m venv .venv`, then the same activation/pip commands; system speech and microphone permissions still need checking.

The `ensurepip` step puts pip into the selected Python environment if it isn't there already. That can happen when reusing a `.venv` created by uv. If `python -m pip` says "No module named pip" but bare `pip` works, don't assume they're the same installation: `pip` may be coming from elsewhere on PATH. Use `python -m pip --version` to check that its path is inside this repository's `.venv`. On Linux, if `ensurepip` itself is missing, install the matching Python venv package from the prerequisite step before trying again.

Those extras install PyAudio for the microphone and faster-whisper for recognition, alongside IRIS's base dependencies (including pyttsx3 and sounddevice). You don't need to hunt down each Python module yourself. This default setup doesn't need a system FFmpeg installation or CUDA for speech recognition.

**Alternatively, use uv for a locked installation.** Pick this route or the pip route above; you don't need both. Install [uv](https://docs.astral.sh/uv/getting-started/installation/) first:

Windows (PowerShell):

```powershell
winget install --id astral-sh.uv --exact
```

Linux (Bash, using uv's official installer):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new terminal afterwards so `uv` is on PATH, and return to the repository. On **either system**:

```sh
uv sync --locked --python 3.12 --extra microphone --extra whisper
uv run --no-sync python iris.py --help
```

uv can download Python 3.12 if it isn't installed. It creates `.venv` and installs the versions in `uv.lock`; no activation is needed. For the rest of this guide, replace `python` with `uv run --no-sync python` if you chose this route. The pip instructions use the dependency ranges in `pyproject.toml`; the lockfile is the reproducible route.

uv manages packages without needing pip inside `.venv`. If you stay on this route, use `uv sync` with the appropriate extras rather than the pip commands above. If you switch to the pip route later, run the `ensurepip` step first.

If you used pip, reactivate the environment when you open another terminal: `.\.venv\Scripts\Activate.ps1` on Windows, `source .venv/bin/activate` on Linux. Or keep using the explicit environment Python path.

### 1. Get your model server working first

[KoboldCPP](https://github.com/LostRuins/koboldcpp/wiki) would be my go-to these days. Load a GGUF chat/instruct model in its launcher and check you can chat with it in its own interface. IRIS doesn't load the chat model itself. The usual KoboldCPP base URL is `http://127.0.0.1:5001/v1`.

Get the executable from the [official KoboldCPP releases](https://github.com/LostRuins/koboldcpp/releases/latest): `koboldcpp.exe` for Windows or `koboldcpp-linux-x64` for Linux with NVIDIA/CUDA support. There are smaller `nocuda` builds for CPU/Vulkan use; pick the build for your machine. These are standalone downloads, not Python packages to install into IRIS's environment.

Windows (PowerShell, in the directory where you saved it):

```powershell
.\koboldcpp.exe
```

Linux (Bash, in the download directory):

```bash
chmod +x koboldcpp-linux-x64
./koboldcpp-linux-x64
```

In the launcher, select your GGUF, choose the acceleration backend/GPU layers appropriate to your machine, and load it. Leave this process running in its own terminal while IRIS runs in another. Open `http://127.0.0.1:5001` and make sure the model answers there first. Keep it local; you don't need a tunnel or public server for this.

For Gemma 4 26B MoE, one download source is [ggml-org's Gemma 4 26B A4B instruct GGUF repository](https://huggingface.co/ggml-org/gemma-4-26B-A4B-it-GGUF/tree/main). Use its **Files and versions** tab and choose a quantisation that fits your machine, including room for context. A split GGUF needs all its parts in the same directory. IRIS only sends text, so it doesn't need an image/audio projector. Use a current KoboldCPP release and the model's matching chat template/Jinja support for the chat-completions API; don't carry over the old Vicuna template from these early experiments. Check KoboldCPP's console if replies look oddly formatted. The correct settings depend on the model file; this isn't a claim that every Gemma quant has been tested here.

I've also had good results with [llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md), although I found it harder to set up and to see what was going back and forth. A server launch looks like this; replace the model path with your own:

```sh
llama-server --model path/to/model.gguf --alias local-model --host 127.0.0.1 --port 8080
```

That's the Linux form. With a Windows binary in your current directory, use `.\llama-server.exe --model "C:\Models\model.gguf" --alias local-model --host 127.0.0.1 --port 8080` in PowerShell.

For that, set IRIS's base URL to `http://127.0.0.1:8080/v1`. Add the acceleration settings appropriate to your hardware; the command above isn't a GPU tuning recipe. KoboldCPP ran faster in my setup, but I haven't done a like-for-like comparison.

[Ollama](https://docs.ollama.com/api/openai-compatibility) uses `http://127.0.0.1:11434/v1`; set `model` to the name of a model you've pulled locally. Current text-generation-webui can also provide a compatible API. These servers share the chat endpoint, but their model names, chat templates, supported parameters and defaults can differ.

Run `python iris.py --check` to see the model IDs advertised by the server, then `python configure.py` to choose the address, model name, recogniser, voice engine, activation and sounds. If your server uses another address/port, configure that first and rerun the check. The script writes `local-config.json`, which stays out of Git. The default model name is `local-model`; use the ID your server advertises when it requires a particular name. You don't need to create an Oobabooga character called Iris any more; `system_prompt` supplies her instructions.

For a first KoboldCPP + faster-whisper run, these are the choices I'd make:

| Question | Answer |
| --- | --- |
| Chat base URL | `http://127.0.0.1:5001/v1` (unless you changed its port) |
| Model name | Copy the model ID from `--check` |
| Speech recognition | `whisper` |
| Speech output | `pyttsx3` |
| Start listening | `enter` |
| Feedback sounds | `yes`, or `no` if you prefer |
| Allow remote services | `no` when both programs run on this machine |

The same answers apply on Windows and Linux. Leave Whisper at CPU/int8 for the first try, especially if your LLM is already using the GPU. Its default `base.en` is an English recognition model; it's separate from the Gemma model you've loaded into KoboldCPP.

### 2. Try a text conversation

```sh
python iris.py --check
python iris.py --text
```

These commands work on both systems after environment setup. The check asks the server for its model list. Some compatible servers don't implement that route; if it fails, check the server console and try text mode. Text mode doesn't load a speech model, initialise a voice, or open an audio device. Type `exit` to finish.

To see what IRIS is sending and receiving, add `--trace`. It prints the conversation and request parameters to the terminal, not a log file. API keys aren't included. Leave it off when you don't want conversation text in diagnostic output. This shows IRIS's messages, not the final prompt your server builds with its chat template; use the server's diagnostics for that.

### 3. Set up speech, then talk

```sh
python iris.py --download-models
python iris.py --list-microphones
python iris.py
```

These commands are also shared by Windows and Linux. The download command is the explicit online setup step for the selected recogniser. For the default `base.en`, faster-whisper fetches the converted model from Hugging Face into the usual model cache; no manual speech-model download is needed. Leave the internet available until this command finishes. Ordinary startup only loads local/cached speech model files. You can choose a different model or a local model directory in the config. This isn't a claim that one model will be best on every machine.

The microphone list shows indices as well as names. If the default is wrong, edit `local-config.json` and set `"microphone_index": 3` (substitute your device's index). You don't need another Python package for that step.

Hugging Face may print a couple of warnings during that first download. If the progress reaches 100% and IRIS says "Done", neither of these means the model failed to download:

- **Xet isn't installed:** the downloader uses ordinary HTTP instead. That's fine; Xet is an optional transfer helper, not part of recognising your speech. If downloads are slow and you'd like to try it, `python -m pip install hf_xet` in the activated IRIS environment works on supported Windows/Linux platforms. It's unnecessary to repeat a successful model download just for this.
- **Windows can't create cache symlinks:** the cache still works, but may use more disk space because it can't share duplicate files as efficiently. IRIS doesn't require administrator rights or Developer Mode. See [Hugging Face's cache explanation](https://huggingface.co/docs/huggingface_hub/guides/manage-cache) if you want to change how that works. Linux usually supports symlinks already, though the filesystem can matter there too.

If you understand that cache tradeoff and just want to hide the symlink warning, set `$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'` in Windows PowerShell, or `export HF_HUB_DISABLE_SYMLINKS_WARNING=1` in Linux Bash, before starting IRIS. That hides the warning; it doesn't change how caching works. There's no need to do this for normal use.

With Enter activation, press Enter, wait for the listening cue, and speak. With Space activation on Windows, wait for the "ready: hold Space" message, keep the IRIS terminal focused, hold Space while speaking, and release it to send the turn. Say `exit` to finish, or use Ctrl-C. Automatic modes wait up to ten seconds for speech to begin and finish a turn on a pause; Space mode uses key release instead. The thirty-second recording cap still applies to both, and you can adjust it with `phrase_time_limit`. This is a turn-by-turn conversation: she finishes speaking before listening again.

If you don't want to install the speech dependencies yet, install just `python -m pip install -e .` and use text mode first.

## Configuration and privacy

The complete set of defaults is in `config_template.json`; copy settings you want to change into `local-config.json`, or use the configuration script. Unknown fields and invalid choices produce an error before the microphone opens. Environment variables named `IRIS_<SETTING>` override the file, which overrides the built-in defaults. For example, `IRIS_MODEL`, `IRIS_BASE_URL` and `IRIS_MAX_TOKENS`. Use `--config path/to/config.json` to choose another file.

By default, chat is restricted to a loopback address and speech uses local engines. A local address can't tell us whether your server is forwarding to a cloud model, so check its settings too. There is no automatic switch to a hosted service if something fails, and IRIS doesn't save recordings or conversation history to disk.

"OpenAI-compatible" is the API format, not a requirement to use OpenAI or ChatGPT. To use a hosted endpoint, or another machine on your LAN, set its exact `base_url` and `model`, and explicitly set `allow_remote` to `true`. That setting also permits the Google speech options. Audio goes to Google with online recognition; reply text goes to Google with gTTS; conversation messages go to whichever chat endpoint you've configured. Choosing a remote endpoint doesn't make the local speech engines remote.

Put credentials in `IRIS_API_KEY`, not your JSON file. The old documentation's `OPEN_AI_API_KEY` spelling was wrong; this refresh consistently uses `IRIS_API_KEY`. A hosted provider may have requirements beyond the small chat-completions subset used here.

For example, to override the model just for this terminal, use `$env:IRIS_MODEL = 'your-model-id'` in Windows PowerShell or `export IRIS_MODEL='your-model-id'` in Linux Bash. Set `IRIS_API_KEY` the same way if your server requires it. These terminal settings take priority over the config file; open a fresh terminal or clear them when you want the file's value again.

Old module-name configuration fields are recognised for the existing speech, activation and sound choices. An old Oobabooga config points to its current `/v1` API on port 5000 and prints a migration notice; your server needs that API enabled. An old GPT config requires an explicit endpoint and a current model name. Back up that file and run the configuration script afresh if you'd rather start clean.

## Speech choices

### Speech-to-text: Whisper, wav2vec2, or Google

Whisper is the new default, through [faster-whisper](https://github.com/SYSTRAN/faster-whisper). It includes voice activity filtering so we can skip non-speech regions when transcribing. We still record the turn before transcription; this isn't live partial transcription as you speak. CPU int8 is the simple starting point. GPU inference needs the CUDA/cuDNN setup required by the installed CTranslate2 version; see the upstream instructions before switching `device` and `compute_type`.

The original wav2vec2 option is still there: run `python -m pip install -e ".[wav2vec2]"` in the activated environment on either platform, choose `wav2vec2`, and run the model download step. With uv, use `uv sync --locked --python 3.12 --extra microphone --extra wav2vec2` instead. Its audio conversion now explicitly resamples to 16 kHz and normalises PCM values, with inference mode enabled.

Google recognition uses SpeechRecognition's online adapter. Choose `google` and enable remote access if that's what you want. It isn't part of the private setup.

### Text-to-speech: Kokoro, system voices, or Google

#### Kokoro: a local neural voice

If you'd like something a bit less like the computer's built-in voice, try Kokoro. IRIS uses the ONNX version on CPU, so it doesn't need a second GPU setup alongside your LLM. The model stays loaded between replies. There is no hosted speech service involved.

In your activated IRIS environment, install the extra on **Windows or Linux**:

```sh
python -m pip install -e ".[microphone,whisper,kokoro]"
```

If you've opened a new terminal, activate it first with `.\.venv\Scripts\Activate.ps1` on Windows or `source .venv/bin/activate` on Linux. You can also use `.\.venv\Scripts\python.exe` or `./.venv/bin/python` directly. With uv, use this on either system instead of pip:

```sh
uv sync --locked --python 3.12 --extra microphone --extra whisper --extra kokoro
```

Then, on either system (use `uv run --no-sync python` instead of `python` if you chose uv):

```sh
python configure.py
python iris.py --download-voices
python iris.py --test-voice
python iris.py
```

Choose `kokoro` for speech output. The configuration script prints a few voice suggestions and lets you choose one there and then. I'd try Heart first, or Emma if you want British English:

| Voice ID | Voice | Suggested use |
| --- | --- | --- |
| `af_heart` | Heart, American female | Default starting point; the upstream voice guide rates it highly. |
| `bf_emma` | Emma, British female | A good starting point if you'd prefer a British voice. |
| `bm_george` | George, British male | One to try for the original Jarvis-ish idea. |
| `bm_fable` | Fable, British male | Another British male option to compare with George. |
| `af_bella` | Bella, American female | An alternative to Heart. |
| `am_michael` | Michael, American male | An American male option. |

Those are suggestions, not a promise that everyone will prefer the same voice. The labels and voice IDs come from the [Kokoro voice guide](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md). `python iris.py --list-voices` shows the suggestions without loading a model. You can change `kokoro_voice` in `local-config.json` and rerun `--test-voice` without involving the microphone or LLM. The voice's accent chooses British/American pronunciation automatically. `kokoro_speed` defaults to `1.0`; values below that slow it down, and values above it speed it up (supported range `0.5` to `2.0`). Try `"kokoro_speed": 1.3` if the normal pace feels a bit leisurely. The configure script asks for this too; restart IRIS after changing it, or use `python iris.py --test-voice` to hear a sample. The system-voice `rate` setting isn't used by Kokoro.

`--download-voices` downloads the fixed [Kokoro ONNX v1.0 model and voice pack](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0), about **354 MB** in total. That includes the voices above, rather than a separate model download for each one. It checks file sizes and SHA-256 hashes, reuses verified downloads, and doesn't replace an existing file with a partial download. `--download-models` also includes Kokoro when it's your selected TTS engine, so it can prepare both recognition and synthesis together.

By default these files live in `C:\Users\<you>\.cache\iris\kokoro` on Windows or `~/.cache/iris/kokoro` on Linux. Set `kokoro_model_dir` to another directory if you prefer. Ordinary startup and synthesis only read those local files; missing files produce a setup instruction instead of an automatic download. The extra includes its eSpeak NG phonemizer dependency; this ONNX path doesn't need PyTorch or a separate Kokoro server. Linux still needs the normal PortAudio/device setup described above. The system voice remains selectable as `pyttsx3` if you want to switch back.

#### System voices and Google

pyttsx3 uses your installed system voices. It starts with the system default now, rather than assuming you've got my particular Australian Windows voice. Set `voice` to an installed voice ID if you have a preference. I found this a useful, quick option for numbers and things like "St Peters" versus "Peter St." Voice quality and pronunciation depend on the installed engine, of course.

On Windows, IRIS pins pyttsx3 to 2.98. Version 2.99 has a [reported SAPI regression](https://github.com/nateshmbhat/pyttsx3/issues/419) where the greeting speaks but later replies are silently skipped. If you installed an earlier revision of IRIS and hit that, stop IRIS, run `python -m pip install -e ".[microphone,whisper]"` in its activated environment, and start it again. On the uv route, rerun your locked sync command instead. Linux keeps its separate pyttsx3 version range; this pin addresses the Windows driver issue.

gTTS is still available if you don't mind sending reply text online: run `python -m pip install -e ".[online]"` in the activated environment on either platform, choose `gtts`, and enable remote access. With uv, add `--extra online` to your sync command, retaining the microphone/recogniser extras you're using. Its MP3 playback needs **both ffmpeg and ffprobe executables on PATH**. Check `ffmpeg -version` and `ffprobe -version` in a new terminal after installation. This is separate from the feedback sounds, which no longer need them.

For that optional FFmpeg setup, Windows users can follow the Windows builds linked from [FFmpeg's download page](https://ffmpeg.org/download.html), extract the tools, and add the directory containing `ffmpeg.exe` and `ffprobe.exe` to their user PATH. On Ubuntu/Debian Linux, `sudo apt install ffmpeg` supplies the tools; use your distribution's equivalent elsewhere. Skip this entirely for the default local speech setup.

### What about newer speech models?

There are more interesting choices now than when I started this. Kokoro is integrated above; [Piper](https://github.com/OHF-Voice/piper1-gpl) would be another local option to compare. [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-1.7B) and [Qwen3-TTS](https://arxiv.org/abs/2601.15621) also give us examples of streaming recognition and controllable, streaming synthesis. Piper and those Qwen models aren't integrated here. Bark was suggested in an issue too; I haven't made it a dependency or tested it here.

The useful comparison is how well a voice handles ordinary replies, how long it takes to start speaking, and what hardware it needs. Starting speech sentence by sentence, recognising the end of a turn, and allowing interruptions would make good follow-up lessons. For now, I've kept the basic pipeline visible rather than hiding it inside an all-in-one voice service.

## Activation and feedback

- **Enter:** the portable terminal option. Press once to start a turn.
- **Space:** hold-to-talk on Windows. Wait for the ready message, hold Space while speaking, then release it. Pauses don't end the recording while you're holding it. The microphone is already open before readiness is announced, with a tiny local lead-in buffer (about 0.2 seconds) to protect opening syllables; older waiting audio is discarded. It doesn't calibrate or play a listening beep after your keypress. If you reach the recording time limit, release Space before starting another turn. Linux users should choose Enter or Continuous; the Space key-state adapter is Windows-specific and needs the terminal focused to start a turn.
- **Continuous:** listen whenever it's your turn, finishing on a pause. This is still available if you prefer using a physical microphone mute button; it hasn't been replaced by the Space option.
- **Wake:** the original "Iris, pay attention" / "Hey Iris" idea, using the selected local recogniser. Say "stop paying attention" to return to idle. This still transcribes short recordings to find the phrase; it isn't a dedicated wake-word model. It requires local recognition, and can miss phrases or take a while in a noisy room.

The beeps and bleeps help when you're away from the screen. They're original procedural WAV cues; see [the sound README](iris_sounds/README.md) to regenerate or replace them. Missing or unplayable feedback falls back to silence. Set `sounds` to `false` if you prefer a quiet life.

## If it gets stuck

- **Microphone problems:** check OS microphone permissions, use `--list-microphones`, and set `microphone_index` if the default is wrong. Bluetooth devices sometimes connect as speakers without enabling the microphone; I used to have to reconnect my teleconferencing puck.
- **Only the greeting is spoken on Windows:** check `python -m pip show pyttsx3`. If it says 2.99, reinstall the project dependencies as described in the system-voices section, then restart IRIS. No extra voice setting is needed for the default system voice.
- **Missing model:** run `--download-models` during setup. Offline startup deliberately won't fetch missing files. Check the selected recogniser's optional dependencies too.
- **Connection, model or HTTP errors:** get a text conversation working first and check the server's own console. Context limits and prompt templates are the server's business; increasing IRIS's timeout won't make an overloaded model faster.
- **Funny apostrophes:** the old Oobabooga display history could contain HTML-escaped text. The new chat route uses plain replies. If your particular server still escapes them, set `reply_encoding` to `html`, or `double-html` for double-escaped replies. Leave it at `text` otherwise, so a discussion of literal HTML entities isn't rewritten.
- **FFmpeg warnings:** these should no longer come from bundled feedback. Check whether you've selected gTTS, which still decodes MP3.
- **A missing optional package:** install the matching extra in the same environment where you run IRIS. Installing Whisper doesn't install the old wav2vec2 stack, or vice versa.

## Reading and checking the code

`iris.py` puts the pieces together and owns the microphone. `iris_chat_engines/compatible.py` sends text messages and owns the conversation history. Speech, activation and feedback adapters live in their existing directories. `iris_settings.py` checks configuration with Pydantic; `iris_audio.py` is the small PCM conversion step.

Type hints help catch mistakes while writing Python. Pydantic checks the configuration and server data when the program actually runs. They're doing different jobs. Optional model libraries are imported only when their adapter is selected; importing IRIS doesn't start listening or download a model.

For development:

```sh
python -m pip install -e ".[dev]"
ruff check .
ruff format --check .
mypy
pytest
```

Or use `uv sync --locked --extra dev` and run those commands through `uv run --no-sync`. Tests use fake devices/models and a mock HTTP transport, so they don't need a microphone, GPU, API key or downloads. CI runs the deterministic checks on Windows and Linux. Automated checks aren't a listening test: real voice quality, offline inference and server-specific model behaviour still need checking on your setup.
