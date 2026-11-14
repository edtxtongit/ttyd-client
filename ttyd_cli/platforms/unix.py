"""
Unix/Linux/macOS terminal input handler.
"""

import os
import select
import sys
import termios
import tty
from collections.abc import Callable

from .base import TerminalInputHandler


class UnixInputHandler(TerminalInputHandler):
    """
    Terminal input handler for Unix-like systems.
    """

    def __init__(self, send_callback: Callable[[str | bytes], None]) -> None:
        """
        Initialize Unix input handler.

        Args:
            send_callback: Function to call when sending input to remote
        """
        super().__init__(send_callback)
        self.original_attrs: list | None = None
        self._stdin_fd: int | None = None
        self._terminal_active = False
        self._running = False

        try:
            self._stdin_fd = sys.stdin.fileno()
            self.original_attrs = termios.tcgetattr(self._stdin_fd)
        except (AttributeError, OSError, ValueError, termios.error):
            pass

    def read_input(self) -> bytes | None:
        """
        Read available bytes without interpreting escape sequences.
        """
        if not self.connected or self._stdin_fd is None:
            return None

        try:
            readable, _, _ = select.select([self._stdin_fd], [], [], 0.05)
            if not readable or not self.connected:
                return None
            data = os.read(self._stdin_fd, 4096)
        except InterruptedError:
            return None
        except (OSError, ValueError):
            self.stop()
            return None

        if not data:
            self.stop()
            return None
        return data if self.connected else None

    def run(self) -> None:
        """
        Forward raw input until stopped, restoring terminal state on exit.
        """
        with self._state_lock:
            if not self.connected or self._running:
                return
            if self._stdin_fd is None:
                self.stop()
                return
            self._running = True

        try:
            with self._state_lock:
                if not self.connected:
                    return
                if self.original_attrs is not None:
                    try:
                        self.original_attrs = termios.tcgetattr(self._stdin_fd)
                        self._terminal_active = True
                        tty.setraw(self._stdin_fd, termios.TCSANOW)
                    except (OSError, ValueError, termios.error):
                        self.stop()
                        return

            while self.connected:
                data = self.read_input()
                if data:
                    self._send_input(data)
        finally:
            with self._state_lock:
                self.stop()
                self.restore_terminal()
                self._running = False

    def restore_terminal(self) -> None:
        """
        Restore terminal state once for an active raw-mode session.
        """
        with self._state_lock:
            if not self._terminal_active:
                return
            try:
                termios.tcsetattr(self._stdin_fd, termios.TCSANOW, self.original_attrs)
            except (OSError, ValueError, termios.error):
                return
            self._terminal_active = False
