# Interface that all the 'push to talk' modules must implement
from abc import ABC, abstractmethod


class IPTT(ABC):

    @abstractmethod
    def __init__(self, settings, stt_engine):
        pass

    @abstractmethod
    def wait_to_talk(self):
        pass
