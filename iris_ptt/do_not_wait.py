# This version doesn't wait at all - useful if your mic has a mute button
from iris_ptt.IPTT import IPTT


class PTT(IPTT):
    def __init__(self, settings, stt_engine):
        pass

    # Wait for the user to press space
    def wait_to_talk(self):
        return True
