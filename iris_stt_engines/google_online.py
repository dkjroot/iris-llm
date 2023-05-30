# This version uses google's online speech to text engine
from iris_stt_engines.ISTT import ISTT
import speech_recognition as sr


class STT(ISTT):
    r = sr.Recognizer()

    def __init__(self, settings):
        pass

    def recognize(self, audio):
        return self.r.recognize_google(audio)

    def is_offline(self):
        return False
