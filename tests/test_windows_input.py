"""
Tests for Windows keyboard mapping and shutdown.
"""

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ttyd_cli.platforms.base import TerminalInputHandler

WINDOWS_SOURCE = Path(__file__).resolve().parents[1] / "ttyd_cli" / "platforms" / "windows.py"
WINDOWS_SPEC = importlib.util.spec_from_file_location(
    "ttyd_cli.platforms._windows_input_test", WINDOWS_SOURCE
)
windows_module = importlib.util.module_from_spec(WINDOWS_SPEC)
with patch.dict(sys.modules, {"msvcrt": Mock()}):
    WINDOWS_SPEC.loader.exec_module(windows_module)


class TestWindowsInputHandler(unittest.TestCase):
    """Exercise the Windows branch with simulated console input."""

    def setUp(self):
        self.console = Mock()
        self.console.kbhit.return_value = True
        self.console_patch = patch.object(windows_module, "msvcrt", self.console)
        self.console_patch.start()
        self.addCleanup(self.console_patch.stop)
        self.send_callback = Mock()
        self.handler = windows_module.WindowsInputHandler(self.send_callback)
        self.assertIsInstance(self.handler, TerminalInputHandler)

    def test_function_keys(self):
        mappings = (
            (";", "\x1bOP"),
            ("<", "\x1bOQ"),
            ("=", "\x1bOR"),
            (">", "\x1bOS"),
            ("?", "\x1b[15~"),
            ("@", "\x1b[17~"),
            ("A", "\x1b[18~"),
            ("B", "\x1b[19~"),
            ("C", "\x1b[20~"),
            ("D", "\x1b[21~"),
            ("\x85", "\x1b[23~"),
            ("\x86", "\x1b[24~"),
        )
        for prefix in ("\x00", "\xe0"):
            for key_code, expected in mappings:
                with self.subTest(prefix=prefix, key_code=key_code):
                    self.console.getwch.side_effect = [prefix, key_code]
                    self.assertEqual(self.handler.read_input(), expected)

    def test_navigation_keys(self):
        mappings = (
            ("H", "\x1b[A"),
            ("P", "\x1b[B"),
            ("M", "\x1b[C"),
            ("K", "\x1b[D"),
            ("G", "\x1b[H"),
            ("O", "\x1b[F"),
            ("I", "\x1b[5~"),
            ("Q", "\x1b[6~"),
            ("R", "\x1b[2~"),
            ("S", "\x1b[3~"),
        )
        for key_code, expected in mappings:
            with self.subTest(key_code=key_code):
                self.console.getwch.side_effect = ["\xe0", key_code]
                self.assertEqual(self.handler.read_input(), expected)

    def test_unsupported_keys_have_no_sequence(self):
        for key_code in ("\x54", "\x8d", "\xff"):
            with self.subTest(key_code=key_code):
                self.console.getwch.side_effect = ["\x00", key_code]
                self.assertIsNone(self.handler.read_input())

    def test_regular_and_control_characters_are_preserved(self):
        for character in ("a", "中", "\r", "\x03", "\x1b"):
            with self.subTest(character=character):
                self.console.getwch.side_effect = [character]
                self.assertEqual(self.handler.read_input(), character)

    def test_no_pending_input_does_not_read(self):
        self.console.kbhit.return_value = False
        self.assertIsNone(self.handler.read_input())
        self.console.getwch.assert_not_called()

    def test_stopped_before_start_does_not_read(self):
        self.handler.stop()
        self.handler.run()
        self.assertIsNone(self.handler.read_input())
        self.console.kbhit.assert_not_called()
        self.console.getwch.assert_not_called()
        self.send_callback.assert_not_called()

    def test_input_read_during_stop_is_not_sent(self):
        def stop_after_read():
            self.handler.stop()
            return "late input"

        with patch.object(self.handler, "read_input", side_effect=stop_after_read):
            self.handler.run()
        self.send_callback.assert_not_called()

    def test_run_sends_mapped_key(self):
        self.console.getwch.side_effect = ["\x00", ";"]
        self.send_callback.side_effect = lambda data: self.handler.stop()
        self.handler.run()
        self.send_callback.assert_called_once_with("\x1bOP")

    def test_stop_while_idle_exits(self):
        self.console.kbhit.return_value = False
        with patch.object(
            windows_module.time, "sleep", side_effect=lambda timeout: self.handler.stop()
        ):
            self.handler.run()
        self.send_callback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
