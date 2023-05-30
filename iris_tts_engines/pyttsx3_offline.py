from iris_tts_engines.ITTS import ITTS
import pyttsx3
import sounddevice as sd


class TTS(ITTS):
    settings = {}
    tts_engine = None

    def __init__(self, settings):
        self.settings = settings
        self.tts_engine = pyttsx3.init()
        self.tts_engine.setProperty('rate', settings['rate'])
        self.tts_engine.setProperty('voice', settings['voice'])

    def speak(self, text):
        sd.stop()
        self.tts_engine.say('<pitch middle="6">' + text + '</pitch>')
        self.tts_engine.runAndWait()
