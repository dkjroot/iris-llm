# Interface that all chat engines must implement
from abc import ABC, abstractmethod


class IChatEngine(ABC):

    @abstractmethod
    def __init__(self, settings, stt_inst, sounds_inst):
        pass

    @abstractmethod
    def listen_for_user_input(self):
        pass

    @abstractmethod
    def get_response(self, user_text):
        pass
