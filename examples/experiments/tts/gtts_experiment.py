"""
A very simple script that uses gTTS to retrieve speech from Google's "Google Translate text-to-speech API". This method
of text to speech uses online services and sends your text to the internet, so is not private. However, it is free to
use (as far as I can tell!) and does a really good job of pronunciation, e.g. if you give it "St Peters" it'll say
"Saint Peters", whereas if you give it "101 Peter St." it'll say "Peter Street". It can also pronounce numbers
reasonably well, e.g. "The answer is 1034" is pronounced as "The answer is ten thirty-four". It has a few languages, I
tend to like the sound of Australian or Irish synthesized voices. My main problem with it (apart from the privacy
issue) is that it speaks really slowly and there's nothing you can do to speed it up.
"""

from gtts import gTTS
from pydub import AudioSegment
from pydub.playback import play
from io import BytesIO


def speak(text):
    mp3_fp = BytesIO()
    tts = gTTS(text, lang='en', tld='com.au')
    tts.write_to_fp(mp3_fp)
    mp3_fp.seek(0)
    play(AudioSegment.from_file(mp3_fp, format="mp3"))


if __name__ == "__main__":
    speak("St Peters")
    speak("101 Peter St.")
    speak("The answer is 1034")
