"""No global keyboard hooks needed: just wait for a line in the terminal."""


class PTT:
    def wait_to_talk(self) -> bool:
        input("Press Enter to talk (Ctrl-C to finish)... ")
        return True
