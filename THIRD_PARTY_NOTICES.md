# Third-party dependencies and credits

Reviewed 20 October 2026 by inspecting the current application's imports and
configuration. These libraries are installed separately, not copied into this
source tree. Direct dependencies and optional extras are now declared in
`pyproject.toml`, with a resolved `uv.lock`. This table identifies upstream
projects, not every transitive or platform dependency in that lockfile.
Each library's authors retain their copyright and license.

| Component | Upstream credit and source | License |
| --- | --- | --- |
| HTTPX | [Encode and contributors](https://github.com/encode/httpx) | BSD-3-Clause |
| Pydantic and pydantic-settings | [Pydantic contributors](https://github.com/pydantic/pydantic) and [settings contributors](https://github.com/pydantic/pydantic-settings) | MIT |
| faster-whisper (optional) | [SYSTRAN and contributors](https://github.com/SYSTRAN/faster-whisper) | MIT; CTranslate2, PyAV and other dependencies retain their own notices |
| kokoro-onnx (optional) | [thewh1teagle and contributors](https://github.com/thewh1teagle/kokoro-onnx) | MIT; model/voice assets have separate Apache-2.0 terms |
| ONNX Runtime (optional) | [Microsoft and contributors](https://github.com/microsoft/onnxruntime) | MIT, plus bundled third-party notices |
| SpeechRecognition | [Anthony Zhang (Uberi) and contributors](https://pypi.org/project/SpeechRecognition/) | BSD-3-Clause for library code; bundled FLAC executables have separate GPLv2 terms and Sphinx data have their own notices |
| PyAudio, when used for microphones | [Hubert Pham and contributors](https://people.csail.mit.edu/hubert/pyaudio/) | MIT; PortAudio retains its own MIT-style notice |
| Transformers | [Hugging Face and contributors](https://github.com/huggingface/transformers) | Apache-2.0; model weights are separate |
| PyTorch | [PyTorch contributors](https://github.com/pytorch/pytorch/blob/main/LICENSE) | BSD-3-Clause, plus bundled third-party notices |
| NumPy | [NumPy developers](https://github.com/numpy/numpy) | BSD-3-Clause, plus bundled component licenses |
| python-sounddevice | [Matthias Geier and contributors](https://python-sounddevice.readthedocs.io/) | MIT; native PortAudio retains its own notice |

The original optional speech model is
[Jonatas Grosman's Wav2Vec2 large XLSR-53 English](https://huggingface.co/jonatasgrosman/wav2vec2-large-xlsr-53-english),
which declares Apache-2.0 and credits the Facebook/Meta base model. It is downloaded
separately. Arbitrary chat models and replacement speech models are not licensed
by this repository's GPL.

The default recognition option now uses faster-whisper's separately downloaded
`base.en` model. Its [Whisper model origin](https://github.com/openai/whisper)
declares MIT. No speech or chat model weights are bundled with IRIS. The optional
model libraries and their native runtimes must retain their own bundled notices.

The Kokoro option downloads the v1.0 ONNX model and voice pack from
[kokoro-onnx's model release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0).
The [original Kokoro-82M model/voice documentation](https://huggingface.co/hexgrad/Kokoro-82M)
declares Apache-2.0. English voice suggestions are identified in the README;
the adapter currently accepts American/British English voice IDs. The model
files are cached separately, not bundled with this repository or wheel.
The phonemizer/espeakng-loader dependencies include eSpeak NG components;
preserve their bundled licences and source notices when redistributing them.

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
