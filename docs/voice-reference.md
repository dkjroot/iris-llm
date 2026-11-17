# Make the short WAV that Pocket needs

Pocket needs a prepared voice in this Iris integration; `pocket_voice: null` does
not select a bundled fallback. You only do preparation once. Return to step 4 of
[Windows](setup-windows.md#4-prepare-the-voice-and-configure-iris),
[macOS](setup-macos.md#4-prepare-the-voice-and-configure-iris) or
[Ubuntu](setup-linux.md#4-prepare-the-voice-and-configure-iris) afterwards.

If you already have a clean single-speaker WAV, use it. A ten-second recording is
a good starting point. Pocket preparation uses at most the first 30 seconds. Avoid
music, echo and several people speaking. Keep the recording and prepared voice
private, outside anything you commit to Git.

To record your own voice on the host, plug in the USB microphone and choose it
as the operating system's default input in Sound settings. The main Iris Python
environment already includes sounddevice. Save the following as `record_voice.py`
in your `iris-models` folder. This is a small, explicit recording program, not a
command to paste into a Python prompt.

```python
from pathlib import Path
import wave
import sounddevice as sd

output = Path.home() / "iris-models" / "reference.wav"
if output.exists():
    raise SystemExit("reference.wav already exists; move it before recording again.")
rate = int(sd.query_devices(kind="input")["default_samplerate"])
input("Press Enter, then speak naturally for ten seconds: ")
samples = sd.rec(10 * rate, samplerate=rate, channels=1, dtype="int16")
sd.wait()
with wave.open(str(output), "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(rate)
    wav.writeframes(samples.astype("<i2", copy=False).tobytes())
print(f"Saved {output}")
```

On Windows, from the Iris checkout, run
`.\.venv\Scripts\python.exe "$HOME\iris-models\record_voice.py"`.
On macOS/Linux run `.venv/bin/python "$HOME/iris-models/record_voice.py"`.
Allow microphone access for the terminal/Python application if asked. Play the
result in your ordinary media player before preparing it. If no input opens,
check the default device and permissions; don't continue with an empty recording.
A machine without an input device can use a WAV recorded on another machine.

To avoid voice preparation entirely, use Kokoro: install the `kokoro` extra in
addition to `whisper`, choose `kokoro` and a supplied voice in configuration, then
run `python iris.py --download-models`. Keep that extra in later sync commands.
[Server setup](server-setup.md#3-download-the-speech-models) describes that route.
