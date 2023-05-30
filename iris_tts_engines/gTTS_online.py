from iris_tts_engines.ITTS import ITTS
from gtts import gTTS
from pydub import AudioSegment
from pydub.playback import play
from io import BytesIO


class TTS(ITTS):
    settings = {}
    lang = 'en'
    tld = 'com.au'

    def __init__(self, settings):
        self.settings = settings
        self.lang = settings['lang']
        self.tld = settings['tld']

    def speak(self, text):
        mp3_fp = BytesIO()
        tts = gTTS(text, lang=self.lang, tld=self.tld)
        tts.write_to_fp(mp3_fp)
        mp3_fp.seek(0)
        play(AudioSegment.from_file(mp3_fp, format="mp3"))
