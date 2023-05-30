# Interface that all sound modules must implement
from abc import ABC, abstractmethod


class ISounds(ABC):

    @abstractmethod
    def __init__(self, settings):
        pass

    @abstractmethod
    def play_working_sound(self):
        pass

    @abstractmethod
    def play_chirp(self):
        pass

    @abstractmethod
    def play_bleep(self):
        pass

    @abstractmethod
    def play_long_bleep(self):
        pass
