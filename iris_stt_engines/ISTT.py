# Interface that all STT modules must implement
from abc import ABC, abstractmethod


class ISTT(ABC):

    @abstractmethod
    def __init__(self, settings):
        pass

    @abstractmethod
    def recognize(self, audio):
        pass

    @abstractmethod
    def is_offline(self):
        pass
        