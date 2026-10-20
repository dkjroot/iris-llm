# Third-party dependencies and credits

Reviewed 20 October 2026 by inspecting the current application's imports and
configuration. These libraries are installed separately, not copied into this
source tree. There is no pinned dependency manifest here, so the entries identify
upstream projects, not a verified lockfile or every possible platform dependency.
Each library's authors retain their copyright and license.

| Component | Upstream credit and source | License |
| --- | --- | --- |
| OpenAI Python client | [OpenAI and contributors](https://github.com/openai/openai-python) | Apache-2.0 |
| Requests | [Kenneth Reitz and contributors](https://github.com/psf/requests) | Apache-2.0 |
| SpeechRecognition | [Anthony Zhang (Uberi) and contributors](https://pypi.org/project/SpeechRecognition/) | BSD-3-Clause for library code; bundled FLAC executables have separate GPLv2 terms and Sphinx data have their own notices |
| PyAudio, when used for microphones | [Hubert Pham and contributors](https://people.csail.mit.edu/hubert/pyaudio/) | MIT; PortAudio retains its own MIT-style notice |
| Transformers | [Hugging Face and contributors](https://github.com/huggingface/transformers) | Apache-2.0; model weights are separate |
| PyTorch | [PyTorch contributors](https://github.com/pytorch/pytorch/blob/main/LICENSE) | BSD-3-Clause, plus bundled third-party notices |
| pydub | [James Robert and contributors](https://pypi.org/project/pydub/) | MIT |
| NumPy | [NumPy developers](https://github.com/numpy/numpy) | BSD-3-Clause, plus bundled component licenses |
| python-sounddevice | [Matthias Geier and contributors](https://python-sounddevice.readthedocs.io/) | MIT; native PortAudio retains its own notice |
| pyttsx3 | [Natesh M Bhat and contributors](https://pypi.org/project/pyttsx3/) | MPL-2.0; installed system voices/engines retain separate terms |
| gTTS | [Pierre Nicolas Durette and contributors](https://pypi.org/project/gTTS/) | MIT for client code; the online service's terms are separate |
| FFmpeg/ffprobe used by audio adapters | [FFmpeg authors](https://ffmpeg.org/legal.html) | LGPL-2.1-or-later or GPL depending on build; inspect any distributed binary |

The configured speech model is
[Jonatas Grosman's Wav2Vec2 large XLSR-53 English](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-english),
which declares Apache-2.0 and credits the Facebook/Meta base model. It is downloaded
separately. Arbitrary chat models and replacement speech models are not licensed
by this repository's GPL.

The new bundled WAV cues are procedural project assets with no third-party
recording inputs; see [their README](iris_sounds/README.md). The old MP3 files
had unresolved provenance and are removed from the current tree. Historical
branches and Git history may still contain them; removal here does not grant
permission to redistribute those older copies.

When redistributing libraries or native runtimes, preserve their actual copyright
and license files, Apache notices/change notices, and any required corresponding
source. In particular, retain source availability for MPL-covered files and
applicable GPL/LGPL audio components. SpeechRecognition publishes its bundled
component details in its own README and `LICENSE-FLAC.txt`; keep those notices
visible when packaging it. Merely linking to this inventory is not a substitute
for carrying the required notices with a binary distribution. Review transitives
and model terms for the exact release. See [LICENSING.md](LICENSING.md).
