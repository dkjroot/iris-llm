"""Hold Space on Windows; Enter remains the portable automatic-endpoint option."""

import sys
from collections.abc import Callable

from speech_recognition import AudioData, AudioSource

from iris_ptt.hold_capture import record_while_held


class PTT:
    _key_state: Callable[[int], int]

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise ValueError("Space activation needs Windows; select 'enter' instead")
        import ctypes

        key_state = ctypes.windll.user32.GetAsyncKeyState
        key_state.argtypes = [ctypes.c_int]
        key_state.restype = ctypes.c_short
        self._key_state = key_state

    def is_held(self) -> bool:
        return bool(self._key_state(0x20) & 0x8000)

    def start_requested(self) -> bool:
        if sys.platform != "win32":
            raise ValueError("Space activation needs Windows; select 'enter' instead")
        import msvcrt

        pressed = False
        while msvcrt.kbhit():
            pressed = msvcrt.getwch() == " " or pressed
        # Console input gates the start, so pressing Space in another app doesn't
        # start a recording. Key-repeat events aren't mistaken for new turns.
        return pressed and self.is_held()

    def record(self, source: AudioSource, phrase_time_limit: float) -> AudioData:
        return record_while_held(source, self.start_requested, self.is_held, phrase_time_limit)
