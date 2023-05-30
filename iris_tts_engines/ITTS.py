# Interface that all TTS engines must implement
from abc import ABC, abstractmethod


class ITTS(ABC):

    @abstractmethod
    def __init__(self, settings):
        pass

    @abstractmethod
    def speak(self, text):
        pass
