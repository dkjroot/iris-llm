# The setup these guides are working towards

The idea is to explain a setup that has grown one piece at a time, without making
someone new feel they must buy every part before trying Iris. The reference is a
Windows PC doing the model work, a Pi doing audio and display, and an iPhone
connecting through Caddy. The home devices use Matter over Wi-Fi.

This table records the owner's description from 17 November 2026. It is useful
context, not a completed fresh-install test or an exact shopping list.

| Part | Reported setup | Detail still to confirm |
| --- | --- | --- |
| Host | Windows 11; AMD X3D-family CPU; 128 GB RAM; 24 GB graphics card | Exact CPU/GPU model and Windows build. |
| Chat | KoboldCPP and a 26B mixture-of-experts model | Exact GGUF filename, quantization, runtime version and context allocation. |
| Recognition | Iris's local Whisper path, implemented with faster-whisper | Configured Whisper model, device and compute type. |
| Speech | Pocket TTS | Voice basename/reference provenance and installed worker versions. |
| Pi | Raspberry Pi 4B, reported as 8 GB | Exact RAM and Raspberry Pi OS image/version. |
| Display | Aftermarket seven-inch HDMI/USB screen | Model, native resolution, touch mapping and display driver. |
| Audio | USB microphone and USB speaker | Device names/indices, capture rate and ALSA mixer control. |
| Phone | iPhone browser through Caddy on the Windows PC | iOS/browser version, native Windows versus WSL Caddy, certificate store and startup task. |
| Home | Matter over Wi-Fi | Device models/firmware, chip-tool version, pairing method, endpoints and production attestation roots. |
| Siri/startup/recovery | Existing project instructions describe these paths | Current release-checklist results, including reboot and restoration. |

“26B” counts model parameters, not required RAM in gigabytes. A mixture-of-experts
model uses selected parts during each inference step, but the weights still have
to be stored and loaded. Quantization stores weights at reduced numerical
precision; different GGUF variants have different memory needs and quality.
Don't buy hardware based only on an active-parameter count.

The reported model name may refer to Gemma 4 26B A4B; that is an inference from
the description, not a verified filename. [Google's model overview](https://ai.google.dev/gemma/docs/core)
describes that family. Gemini and Gemma are different names; copy the actual
GGUF filename and KoboldCPP configuration when reproducing a setup.

## Record a comparable result

For each installation, record the OS/build and CPU architecture, RAM/GPU/driver,
Python and Iris versions, model filename/hash, KoboldCPP version, context size,
Whisper/Pocket settings, Pi image and audio/display identities. For phone and home,
record the browser/iOS version, proxy deployment type, device firmware, pairing
route and test outcome. Do not include tokens, Wi-Fi passwords, private addresses,
voice recordings or conversation exports in a public record.

Windows can show CPU/GPU/RAM in Task Manager's Performance tab and the OS build
with `winver`. On macOS use About This Mac and System Information. On Ubuntu use
Settings → About, `uname -m` and `lsb_release -ds`. On the Pi use
`cat /etc/os-release`, `uname -m` and `free -h`. `git rev-parse HEAD` identifies Iris;
`uv run --no-sync python --version` identifies the main environment.

The [release checklist](releasing.md) records physical results. Keep untested
combinations visibly pending. A simulated switch or downloaded model is not a
successful conversation, read-back, reboot or restore.
