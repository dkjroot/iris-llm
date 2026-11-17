# Choosing an installation profile

An Iris installation has three separate ingredients: Python packages, native
libraries supplied by the operating system, and model files downloaded during
setup. Installing a Python extra does not prove that a model fits your hardware
or that a microphone or GPU is working.

Use Python 3.11–3.13. Python 3.12 is the reference version for the profile jobs.
For a reproducible source checkout, install uv 0.7.19 and use `uv sync --locked`
with the extras you need. For an ordinary pip installation, install the same
extras on the wheel or checkout; pip resolves within the declared version ranges.
Do not install every speech engine into one environment just because it is available.

| Job | Extras or packages | Other things it needs |
| --- | --- | --- |
| Text client, HTTP interfaces and core memory archive | Base Iris package | Text capture needs no audio device. Starting the server also needs its selected STT/TTS packages and models, even for text clients. |
| Usual PC speech server | `whisper,kokoro` | Downloaded Whisper and Kokoro models; compatible CPU/GPU libraries for the chosen execution mode. |
| Semantic memory | `memory` | The configured embedding model and tokenizer files. Original archive storage does not require embeddings. |
| Pi microphone client | `microphone` | PortAudio and a working input/output device; Linux commonly needs its PortAudio development package to build PyAudio. |
| Local voice activity detection | `vad` | The Silero model, prepared during setup. |
| Local wake and cancel recognition | `vosk` installed separately | A Vosk model directory and the microphone profile. |
| Pi face display | Base package | Pygame/SDL2 from the operating system (`python3-pygame` on the Pi), with a working display/session. |
| Alternative recognition | `wav2vec2` | Matching model files, PyTorch and the appropriate CPU/GPU runtime. |
| Chatterbox or Pocket synthesis | Separate worker environment | Use the pinned worker requirements and preparation commands in [server setup](server-setup.md). |
| Contributor checks | `dev` | Git, localhost access; Node for artwork/JS and Playwright's Chromium for browser checks. |

For example, from a checkout, `uv sync --locked --extra whisper --extra kokoro
--extra memory` prepares the usual server packages. A microphone client can use
`uv sync --locked --extra microphone --extra vad`. Installing Vosk separately
means managing that extra dependency yourself; a later `uv sync` can remove
packages that are not in its selected dependency set. Keep the install commands
in your local launcher consistent with the profile you actually use.

The CI profile matrix installs base, server, semantic-memory and VAD packages
on Windows, macOS and Linux with Python 3.12, then imports their native modules
without loading weights. Base artifact checks additionally cover Python 3.11 and
3.13 on Windows and Linux. These are intended release gates, not evidence that
an unrun workflow or a particular GPU works. PyAudio, Vosk, SDL, wav2vec2 and the
isolated synthesis workers still need the applicable checks on the target device.

After installing a profile, run `python scripts/check_profiles.py PROFILE` from
the source distribution or checkout. Supported names are `base`, `server`,
`memory`, `vad`, `microphone`, `wake`, `wav2vec2`, `kokoro`, `chatterbox` and `pocket`.
Run the last two with the worker environment's Python. The command reports
package versions and import success without loading models, opening devices or
reading your Iris configuration. Missing packages fail clearly rather than
turning the test into a quiet skip.

Follow [server setup](server-setup.md), [client setup](client-setup.md) and the
[release acceptance checklist](releasing.md) for model preparation and the real
conversation checks. Save the platform, Python version, selected extras and
result with each release so an import check isn't mistaken for end-to-end testing.
