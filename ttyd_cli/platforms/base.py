"""
Base class for platform-specific input handlers.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from threading import RLock


class TerminalInputHandler(ABC):
    """
    Abstract base class for terminal input handling.
    """

    def __init__(self, send_callback: Callable[[str | bytes], None]) -> None:
        """
        Initialize input handler.

        Args:
            send_callback: Function to call when sending input to remote
        """
        self.send_callback: Callable[[str | bytes], None] = send_callback
        self.connected: bool = True
        self._state_lock = RLock()

    @abstractmethod
    def read_input(self) -> str | bytes | None:
        """
        Read input from terminal as text or raw bytes.
        """

    @abstractmethod
    def run(self) -> None:
        """
        Main input loop.
        """

    @abstractmethod
    def restore_terminal(self) -> None:
        """
        Restore terminal to original state.
        """

    def _send_input(self, data: str | bytes) -> None:
        """
        Send input only while the handler is running.
        """
        with self._state_lock:
            if self.connected:
                self.send_callback(data)

    def stop(self) -> None:
        """
        Prevent further sends and signal the input loop to stop.
        """
        with self._state_lock:
            self.connected = False
