"""
This module implements the 'hey siri' style function - using a local stt model it listens for the wake word
and blocks until it's heard.  It has two modes - "paying attention", where you only need to say "Iris" or "Hey Iris"
to wake it up, and "idling" where you need to say "Pay attention Iris" to put it into "paying attention" mode.  To go
back to idling, say "Stop paying attention"

One of the problems with the wakeword system is that it might be interpreting some long sentence it's hear while you're
trying to say the wakeword, and you just have to wait silently until it's ready.  It can be tricky to know when it's
ready, which is why it outputs ':' and '.' during the "waiting for wakeword" phase.  ':' means it's ready to listen.  If
you see a '.', that means it's picked up some audio and you'll have to wait for the next ':' before you can say the
wakeword.  I tried using audio prompts for this, but it was too annoying.  This is one of the reasons I prefer to use
a push-to-talk (really a mute mic) button on my bluetooth speaker in later versions of Iris.

If the STT engine in use is an offline model, it will use that.  If the STT model is an online model, this module
will load and use wav2vec2 so that the wakeword detection can be done locally.  This is for privacy reasons.
"""

from iris_ptt.IPTT import IPTT
import speech_recognition as sr
from enum import Enum
from pydub import AudioSegment
import io
import torch
import time


class WakeMode(Enum):
    PAYING_ATTENTION = 1  # While paying attention, the short_wakeword is enough
    IDLING = 2  # While idling, will wait to hear the long_wakeword (and switch to PAYING_ATTENTION)


class PTT(IPTT):
    settings = None

    current_wakelistener_mode = WakeMode.IDLING

    # A few ways in which the model migh mis-hear the wake words!
    long_wakewords = ["pay attention iris", "pay attention firus", "pay attention irus", "pay attention ores",
                      "pay attention harris", "pay attention irce", "iris pay attention", "firus pay attention",
                      "irus pay attention", "ores pay attention", "harris pay attention", "irce pay attention"]
    short_wakewords = ["iris", "firus", "irus", "ores", "harris", "irce", "hey iris", "hey firus", "hey irus",
                       "hey ores", "hey harris", "hey irce", "yo iris", "yo firus", "yo irus", "yo ores", "yo harris",
                       "yo irce"]
    sleepmode_words = ["stop paying attention iris", "iris stop paying attention", "stop paying attention",
                       "stop paying attention firus", "firus stop paying attention",
                       "stop paying attention irus", "irus stop paying attention",
                       "stop paying attention ores", "ores stop paying attention",
                       "stop paying attention harris", "harris stop paying attention",
                       "stop paying attention irce", "irce stop paying attention"]

    stt_inst = None

    # If an offline STT model is being used we'll use it, otherwise we'll load the wav2vec2 model
    w2v2_tokenizer = None
    w2v2_model = None

    # As usual, we'll use sr.Recognizer to listen for user input
    r = sr.Recognizer()

    def __init__(self, settings, stt_inst):
        self.settings = settings

        # Use the existing stt engine if it's an offline model, otherwise load wav2vec2
        if stt_inst.is_offline():
            self.stt_inst = stt_inst
        else:
            self.stt_inst = None
            # I feel like I'm really pushing what it's ok to do with imports here :)
            from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
            self.w2v2_tokenizer = Wav2Vec2Processor.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')
            self.w2v2_model = Wav2Vec2ForCTC.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')

    # Check if the text is either long wakeword or short wakeword
    def is_wakeword(self, text):
        return self.is_long_wakeword(text) or self.is_short_wakeword(text)

    # Check if the text is long wakeword
    def is_long_wakeword(self, text):
        return text in self.long_wakewords

    # Check if the text is short wakeword
    def is_short_wakeword(self, text):
        return text in self.short_wakewords

    # Check if the text is the 'sleep' word that puts us back into idle mode
    def is_sleepmode_word(self, text):
        return text in self.sleepmode_words

    def infer_text(self, audio):
        if self.stt_inst is not None:
            return self.stt_inst.recognize(audio)
        else:
            # Convert to inputs for the model
            data = io.BytesIO(audio.get_wav_data())
            clip = AudioSegment.from_file(data)
            x = torch.FloatTensor(clip.get_array_of_samples())

            # Infer text from voice
            inputs = self.w2v2_tokenizer(x, sampling_rate=16000, return_tensors='pt',
                                         padding='longest').input_values
            logits = self.w2v2_model(inputs).logits
            tokens = torch.argmax(logits, axis=-1)
            text = str(self.w2v2_tokenizer.batch_decode(tokens)[0]).lower()
            return text

    # Wait for the user to press space
    def wait_to_talk(self):
        print("(awaiting wakeword...", end='', flush=True)
        try:
            with sr.Microphone(sample_rate=16000) as source:
                while True:
                    # Sample from mic
                    self.r.adjust_for_ambient_noise(source, duration=0.2)
                    print(":", end='', flush=True)
                    audio = self.r.listen(source)
                    print(".", end='', flush=True)
                    text = self.infer_text(audio)

                    # wake if the time is right!
                    if self.current_wakelistener_mode == WakeMode.IDLING:
                        if self.is_long_wakeword(text):
                            print(") (paying attention...) ", end='', flush=True)
                            self.current_wakelistener_mode = WakeMode.PAYING_ATTENTION
                            return True
                    else:
                        if self.is_sleepmode_word(text):
                            print(") (sleeping...) ", end='', flush=True)
                            self.current_wakelistener_mode = WakeMode.IDLING
                        if self.is_wakeword(text):
                            print(") (waking...) ", end='', flush=True)
                            return True

                    # otherwise yield briefly then listen again
                    time.sleep(0.1)

        except Exception as e:
            print(") \nWake listener exception {0}".format(e))
            return False
