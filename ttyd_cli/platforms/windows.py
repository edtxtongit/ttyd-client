"""
Windows terminal input handler.
"""

import msvcrt
import time
from collections.abc import Callable
from typing import ClassVar

from .base import TerminalInputHandler


class WindowsInputHandler(TerminalInputHandler):
    """
    Terminal input handler for Windows systems.
    """

    KEY_MAP: ClassVar[dict[str, str]] = {
        "H": "\x1b[A",
        "P": "\x1b[B",
        "M": "\x1b[C",
        "K": "\x1b[D",
        "G": "\x1b[H",
        "O": "\x1b[F",
        "I": "\x1b[5~",
        "Q": "\x1b[6~",
        "R": "\x1b[2~",
        "S": "\x1b[3~",
        ";": "\x1bOP",
        "<": "\x1bOQ",
        "=": "\x1bOR",
        ">": "\x1bOS",
        "?": "\x1b[15~",
        "@": "\x1b[17~",
        "A": "\x1b[18~",
        "B": "\x1b[19~",
        "C": "\x1b[20~",
        "D": "\x1b[21~",
        "\x85": "\x1b[23~",
        "\x86": "\x1b[24~",
    }

    def __init__(self, send_callback: Callable[[str | bytes], None]) -> None:
        """
        Initialize Windows input handler.

        Args:
            send_callback: Function to call when sending input to remote
        """
        super().__init__(send_callback)

    def read_input(self) -> str | None:
        """
        Read keyboard input with special key mapping.

        Returns:
            Optional[str]: The input character(s) or mapped escape sequence
        """
        if not self.connected or not msvcrt.kbhit():
            return None

        character = msvcrt.getwch()
        if character in ("\x00", "\xe0"):
            key_code = msvcrt.getwch()
            return self.KEY_MAP.get(key_code)

        return character

    def run(self) -> None:
        """
        Main input loop for Windows.
        """
        while self.connected:
            key = self.read_input()
            if key:
                self._send_input(key)
            else:
                time.sleep(0.01)

    def restore_terminal(self) -> None:
        """
        Restore terminal to original state (no-op on Windows).
        """
