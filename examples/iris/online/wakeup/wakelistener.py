"""
IRIS - Intelligent Residential Interface System
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
"""

from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
import speech_recognition as sr
from enum import Enum
from pydub import AudioSegment
import io
import torch
import time


class WakeMode(Enum):
    PAYING_ATTENTION = 1  # While paying attention, the short_wakeword is enough
    IDLING = 2  # While idling, will wait to hear the long_wakeword (and switch to PAYING_ATTENTION)


current_wakelistener_mode = WakeMode.IDLING

# A few ways in which the model migh mis-hear the wake words!
long_wakewords = ["pay attention iris", "pay attention firus", "pay attention irus", "pay attention ores",
                  "pay attention harris", "pay attention irce", "iris pay attention", "firus pay attention",
                  "irus pay attention", "ores pay attention", "harris pay attention", "irce pay attention"]
short_wakewords = ["iris", "firus", "irus", "ores", "harris", "irce", "hey iris", "hey firus", "hey irus", "hey ores",
                   "hey harris", "hey irce", "yo iris", "yo firus", "yo irus", "yo ores", "yo harris", "yo irce"]
sleepmode_words = ["stop paying attention iris", "iris stop paying attention", "stop paying attention",
                   "stop paying attention firus", "firus stop paying attention",
                   "stop paying attention irus", "irus stop paying attention",
                   "stop paying attention ores", "ores stop paying attention",
                   "stop paying attention harris", "harris stop paying attention",
                   "stop paying attention irce", "irce stop paying attention"]

# Load the model
w2v2_tokenizer = Wav2Vec2Processor.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')
w2v2_model = Wav2Vec2ForCTC.from_pretrained('jonatasgrosman/wav2vec2-large-xlsr-53-english')

# Again we'll use sr.Recognizer to listen for user input
r = sr.Recognizer()


# Check if the text is either long wakeword or short wakeword
def is_wakeword(text):
    return is_long_wakeword(text) or is_short_wakeword(text)


# Check if the text is long wakeword
def is_long_wakeword(text):
    return text in long_wakewords


# Check if the text is short wakeword
def is_short_wakeword(text):
    return text in short_wakewords


# Check if the text is the 'sleep' word that puts us back into idle mode
def is_sleepmode_word(text):
    return text in sleepmode_words


# Listen until the user says the appropriate wake word - also handles switching between modes
def listen_for_wakeword():
    global current_wakelistener_mode
    print("(awaiting wakeword...", end='', flush=True)
    try:
        with sr.Microphone(sample_rate=16000) as source:
            while True:
                # Sample from mic
                r.adjust_for_ambient_noise(source, duration=0.2)
                print(":", end='', flush=True)
                audio = r.listen(source)
                print(".", end='', flush=True)

                # Convert to inputs for the model
                data = io.BytesIO(audio.get_wav_data())
                clip = AudioSegment.from_file(data)
                x = torch.FloatTensor(clip.get_array_of_samples())

                # Infer text from voice
                inputs = w2v2_tokenizer(x, sampling_rate=16000, return_tensors='pt', padding='longest').input_values
                logits = w2v2_model(inputs).logits
                tokens = torch.argmax(logits, axis=-1)
                text = str(w2v2_tokenizer.batch_decode(tokens)[0]).lower()

                # wake if the time is right!
                if current_wakelistener_mode == WakeMode.IDLING:
                    if is_long_wakeword(text):
                        print(") (paying attention...) ", end='', flush=True)
                        current_wakelistener_mode = WakeMode.PAYING_ATTENTION
                        return True
                else:
                    if is_sleepmode_word(text):
                        print(") (sleeping...) ", end='', flush=True)
                        current_wakelistener_mode = WakeMode.IDLING
                    if is_wakeword(text):
                        print(") (waking...) ", end='', flush=True)
                        return True

                # otherwise yield briefly then listen again
                time.sleep(0.1)

    except Exception as e:
        print(") \nWake listener exception {0}".format(e))
        return False
