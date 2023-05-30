# This version plays beeps and bleeps to give audible feedback to the user
from iris_sounds.ISounds import ISounds
from pydub import AudioSegment
from pydub.playback import play
import numpy as np
import sounddevice as sd


class Sounds(ISounds):
    working = AudioSegment.from_mp3('iris_sounds/workinglongquiet.mp3')
    workinga = np.array(working.get_array_of_samples())
    chirp = AudioSegment.from_mp3('iris_sounds/chirp.mp3')
    bleep = AudioSegment.from_mp3('iris_sounds/bleep.mp3')
    longbleep = AudioSegment.from_mp3('iris_sounds/longbleep.mp3')

    def __init__(self, settings):
        pass

    def play_working_sound(self):
        sd.stop()
        sd.play(self.workinga, self.working.frame_rate * 2)

    def play_chirp(self):
        sd.stop()
        play(self.chirp)

    def play_bleep(self):
        sd.stop()
        play(self.bleep)

    def play_long_bleep(self):
        sd.stop()
        play(self.longbleep)
