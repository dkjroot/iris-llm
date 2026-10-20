"""Leave the conversation quiet apart from Iris herself."""

from iris_sounds.ISounds import ISounds


class Sounds(ISounds):
    def __init__(self, settings: dict[str, object]) -> None:
        pass

    def play_working_sound(self) -> None:
        pass

    def play_chirp(self) -> None:
        pass

    def play_bleep(self) -> None:
        pass

    def play_long_bleep(self) -> None:
        pass
