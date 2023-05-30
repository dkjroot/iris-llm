# This version waits for the user to press space.  msvcrt is a Windows-only library.
from iris_ptt.IPTT import IPTT
import msvcrt as m
import time


class PTT(IPTT):
    def __init__(self, settings, stt_engine):
        pass

    # Wait for the user to press space
    def wait_to_talk(self):
        print("(push space to talk...) ", end='', flush=True)
        c = 'a'
        while c != b' ':
            c = m.getch()
            time.sleep(0.1)
        while m.kbhit():  # waste any more input in the buffer before returning
            c = m.getch()
        return True
