# IRIS feedback sounds

The four WAV files here are original procedural cues made for IRIS on 20 October
2026. They use only sine waves and mathematical envelopes, with no recordings,
samples, melodies copied from elsewhere, or external sound-generation service.
The generator and cues are supplied under the repository's
[GNU GPL version 3](../LICENSE), where copyright applies.

| File | Purpose and character |
| --- | --- |
| `chirp.wav` | Ready for push-to-talk: a short, gentle rising chirp. |
| `bleep.wav` | Listening is about to begin: one clear, brief mid/high tone. |
| `longbleep.wav` | Longer feedback cue: two soft descending tones, distinct from the listening cue. Reserved for callers that use it; the current main chat loop does not. |
| `workinglongquiet.wav` | Loading/transcribing/thinking: very quiet, spaced low pulses for 3.6 seconds. Nonblocking; it is not a promise that a task will finish in that time. |

The beeps-and-bleeps provider uses these files locally at their native pitch and
sample rate. It needs `sounddevice` and its normal NumPy/PortAudio dependencies,
but no FFmpeg or MP3 decoder for these cues. Choose `iris_sounds.no_sounds` in
`configure.py` to disable feedback sounds.

## Recreate or change them

Run from the repository directory with Python 3.10 or newer:

In an activated IRIS environment, the following command works in both Windows
PowerShell and Linux Bash. Without activation, use
`.\.venv\Scripts\python.exe iris_sounds/generate_sounds.py` on Windows or
`./.venv/bin/python iris_sounds/generate_sounds.py` on Linux.

```sh
python iris_sounds/generate_sounds.py
```

The script uses only Python's standard library and writes four mono, 24 kHz,
16-bit PCM WAV files beside itself. It makes no network requests and does not
open an audio device. Edit its frequencies, volumes, and durations to make your
own variations. Smooth fades avoid sharp clicks; the working cue is deliberately
quieter than the short cues. Start playback at a comfortable device volume.

## Bring your own sounds

You can instead create or download your own cues and save them under the four
filenames above, as **mono, 16-bit PCM WAV** files. Keep the listening cue short
so it finishes before microphone capture. Use distinctive, gentle sounds with
little background noise; a quiet working pulse should not mask speech.

Use sounds you made or whose explicit terms permit your intended use. CC0 is a
convenient choice for downloaded replacements; check the actual asset's terms,
keep source/author/license details, and meet any attribution obligations. Do not
assume "free download" means free redistribution. Your replacement files are not
automatically licensed by IRIS's GPL. MP3 downloads are ignored by Git. Replacing
a bundled WAV changes a maintained file: keep your replacements local unless
their terms permit redistribution, and retain any required credits.

The old `bleep.mp3`, `chirp.mp3`, `longbleep.mp3`, and `workinglongquiet.mp3` were
removed because their source and redistribution permission could not be verified.
They are not inputs to this generator. Older Git history and the historical
`prototypes` branch may still contain them; their presence there is not license
clearance. Review that history before making the repository public.
