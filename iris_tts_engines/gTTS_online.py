"""Google synthesis is optional; this MP3 path still needs FFmpeg/ffprobe."""

from __future__ import annotations

from io import BytesIO
from shutil import which

from iris_settings import Settings


class TTS:
    def __init__(self, settings: Settings) -> None:
        if not which("ffmpeg") or not which("ffprobe"):
            raise ValueError(
                "gTTS playback needs ffmpeg and ffprobe on PATH. "
                "Use pyttsx3 for local speech without them."
            )
        self.lang = settings.lang
        self.tld = settings.tld

    def speak(self, text: str) -> None:
        from gtts import gTTS
        from pydub import AudioSegment
        from pydub.playback import play

        with BytesIO() as audio:
            gTTS(text, lang=self.lang, tld=self.tld).write_to_fp(audio)
            audio.seek(0)
            play(AudioSegment.from_file(audio, format="mp3"))

    def close(self) -> None:
        pass
