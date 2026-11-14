"""
Tests for input dispatch and stop synchronization.
"""

import threading
import unittest
from unittest.mock import Mock

from ttyd_cli.platforms.base import TerminalInputHandler


class TestInputHandler(TerminalInputHandler):
    """Minimal handler for exercising shared lifecycle behavior."""

    __test__ = False

    def read_input(self):
        return None

    def run(self):
        pass

    def restore_terminal(self):
        pass


class TestTerminalInputHandler(unittest.TestCase):
    def test_dispatch_accepts_text_and_bytes(self):
        callback = Mock()
        handler = TestInputHandler(callback)
        handler._send_input("text")
        handler._send_input(b"\x00\xff")
        self.assertEqual([call.args[0] for call in callback.call_args_list], ["text", b"\x00\xff"])

    def test_stop_prevents_further_dispatch(self):
        callback = Mock()
        handler = TestInputHandler(callback)
        handler.stop()
        handler.stop()
        handler._send_input(b"late")
        self.assertFalse(handler.connected)
        callback.assert_not_called()

    def test_stop_waits_for_an_inflight_callback(self):
        callback_started = threading.Event()
        release_callback = threading.Event()
        stop_started = threading.Event()
        stop_finished = threading.Event()
        received = []
        errors = []

        def callback(data):
            callback_started.set()
            if not release_callback.wait(1):
                errors.append("Callback was not released")
            received.append(data)

        handler = TestInputHandler(callback)

        def stop_handler():
            stop_started.set()
            handler.stop()
            stop_finished.set()

        sender = threading.Thread(target=handler._send_input, args=(b"first",), daemon=True)
        stopper = threading.Thread(target=stop_handler, daemon=True)
        try:
            sender.start()
            self.assertTrue(callback_started.wait(1))
            stopper.start()
            self.assertTrue(stop_started.wait(1))
            self.assertFalse(stop_finished.wait(0.05))
        finally:
            release_callback.set()
            sender.join(1)
            if stopper.ident is not None:
                stopper.join(1)
        self.assertFalse(sender.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertTrue(stop_finished.is_set())
        handler._send_input(b"late")
        self.assertEqual(received, [b"first"])
        self.assertEqual(errors, [])

    def test_callback_can_stop_its_handler(self):
        handler = TestInputHandler(lambda data: handler.stop())
        sender = threading.Thread(target=handler._send_input, args=(b"input",), daemon=True)
        sender.start()
        sender.join(1)
        self.assertFalse(sender.is_alive())
        self.assertFalse(handler.connected)


if __name__ == "__main__":
    unittest.main()
