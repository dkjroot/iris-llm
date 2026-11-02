# IRIS feedback sounds

The WAV files here are original procedural cues made for IRIS. The initial
four cues were added on 20 October 2026, with the error and command acknowledgement cues added on 1 November 2026. They use only sine waves and mathematical envelopes, with no recordings,
samples, melodies copied from elsewhere, or external sound-generation service.
The generator and cues are supplied under the repository's
[GNU GPL version 3](../LICENSE), where copyright applies.

| File | Purpose and character |
| --- | --- |
| `chirp.wav` | Ready at startup, before Enter-to-talk, or when returning to wake waiting: a subtle 90 ms rising chirp. |
| `bleep.wav` | Listening after Enter or an accepted wake phrase: one clear, brief mid/high tone. |
| `longbleep.wav` | Recording finished in Enter mode or a local test: one gentle 100 ms chirp, slightly higher than the ready cue. |
| `workinglongquiet.wav` | Waiting for the server: low pulses in a 3.6-second loop, repeated until the request finishes or fails. |
| `error.wav` | A failed turn, or no speech during Enter-to-talk/a local test: three short low pulses. Hands-free timeouts and unrecognised recordings stay quiet. |
| `acknowledge.wav` | Command accepted: four quick rising chirps, lasting 255 ms altogether. Used for “clear context” and “stop listening”. |

The client owns feedback playback, including in a remote-client setup. Cues use
its selected `--speaker` device and the same sample-rate conversion as spoken
replies. Short cues finish with the microphone closed. The working cue plays
without blocking the server request and stops before speech or further capture,
on request failure and on Ctrl-C. Command acknowledgements play after the server accepts the command and the working sound stops, before capture resumes. “Clear context” keeps an open wake conversation listening. In wake mode, the ready chirp follows microphone preparation;
say the wake phrase after that chirp to open a conversation, then give your first
message after the listening bloop. During an open conversation, continuous mode,
or companion mode, capture quietly resumes after each reply without repeated
ready/listening/recorded cues. There is no need to repeat the wake phrase;
“stop listening” closes the wake window. Optional local speech detection rejects
non-speech before a server request starts, so rejected noise doesn't start the
working cue either. See the main README for setup and sensitivity controls.

Use `python iris.py client --no-sounds` to disable feedback while keeping spoken
replies. These commands and options are shared by Windows PowerShell, macOS and
Linux Bash. Text mode doesn't play cues. Missing or unplayable feedback produces
a warning and disables cues for that run; it doesn't make the conversation depend
on optional sound files. No FFmpeg or MP3 decoder is needed.

## Recreate or change them

Run from the repository directory with Python 3.10 or newer:

In an activated IRIS environment, the following command works in both Windows
PowerShell and Linux Bash. Without activation, use
`.\.venv\Scripts\python.exe iris_sounds/generate_sounds.py` on Windows or
`./.venv/bin/python iris_sounds/generate_sounds.py` on Linux.

```sh
python iris_sounds/generate_sounds.py
```

The script uses only Python's standard library and writes six mono, 24 kHz,
16-bit PCM WAV files beside itself. It makes no network requests and does not
open an audio device. Edit its frequencies, volumes, and durations to make your
own variations. Smooth fades avoid sharp clicks; the working cue is deliberately
quieter than the short cues. Start playback at a comfortable device volume.

## Bring your own sounds

You can instead create or download your own cues and save them under the six
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
