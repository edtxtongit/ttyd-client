"""
Tests for Unix terminal input and lifecycle.
"""

import errno
import os
import threading
import unittest
from unittest.mock import Mock, patch

if os.name != "nt":
    import termios
    import tty

    from ttyd_cli.platforms.unix import UnixInputHandler


@unittest.skipIf(os.name == "nt", "Requires Unix terminal APIs")
class TestUnixInputHandler(unittest.TestCase):
    """Exercise raw input against a real pseudo-terminal."""

    def setUp(self):
        self.master_fd, self.slave_fd = os.openpty()
        self.original_attrs = termios.tcgetattr(self.slave_fd)
        self.stdin = Mock()
        self.stdin.fileno.return_value = self.slave_fd
        self.stdin.read.side_effect = AssertionError("Buffered reads are not allowed")
        self.stdin_patch = patch("ttyd_cli.platforms.unix.sys.stdin", self.stdin)
        self.stdin_patch.start()
        self.received = bytearray()
        self.received_condition = threading.Condition()
        self.handler = UnixInputHandler(self._receive)
        self.worker = None
        self.worker_errors = []

    def tearDown(self):
        try:
            self.handler.stop()
            if self.worker is not None:
                self.worker.join(1)
                self.assertFalse(self.worker.is_alive())
            self.handler.restore_terminal()
            self.assertEqual(self.worker_errors, [])
        finally:
            termios.tcsetattr(self.slave_fd, termios.TCSANOW, self.original_attrs)
            self.stdin_patch.stop()
            os.close(self.slave_fd)
            os.close(self.master_fd)

    def _receive(self, data):
        self.assertIsInstance(data, bytes)
        with self.received_condition:
            self.received.extend(data)
            self.received_condition.notify_all()

    def _start_handler(self):
        read_started = threading.Event()
        original_read = self.handler.read_input

        def observed_read():
            read_started.set()
            return original_read()

        def run_handler():
            try:
                self.handler.run()
            except (
                AssertionError,
                AttributeError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as error:
                self.worker_errors.append(error)

        self.handler.read_input = observed_read
        self.worker = threading.Thread(target=run_handler, daemon=True)
        self.worker.start()
        self.assertTrue(read_started.wait(1))
        self.assertFalse(termios.tcgetattr(self.slave_fd)[3] & termios.ICANON)

    def _wait_for_input(self, payload):
        with self.received_condition:
            self.assertTrue(
                self.received_condition.wait_for(
                    lambda: len(self.received) >= len(payload), timeout=1
                )
            )
            self.assertEqual(bytes(self.received), payload)

    def test_batched_arrow_paste_and_control_bytes(self):
        self._start_handler()
        payload = (
            b"\x1b[A\x1b[B\x1b[C\x1b[D\x1b[200~"
            + "paste 中\nsecond line".encode()
            + b"\x1b[201~\x00\x03\x04\r\n\x7f\xff\x1b"
        )
        self.assertEqual(os.write(self.master_fd, payload), len(payload))
        self._wait_for_input(payload)
        self.stdin.read.assert_not_called()

    def test_escape_sequences_are_not_filtered(self):
        self._start_handler()
        payload = (
            b"\x1b[1;2R\x1b[1;5R\x1b[?1;2c"
            b"\x1b]10;rgb:0000/0000/0000\x07"
            b"\x1bPdata\x1b\\\x1b_kitty\x1b\\"
        )
        os.write(self.master_fd, payload)
        self._wait_for_input(payload)

    def test_pending_input_is_not_flushed_on_start(self):
        payload = b"queued input\n"
        os.write(self.master_fd, payload)
        self._start_handler()
        self._wait_for_input(payload)

    def test_stop_without_input_exits_and_restores_terminal(self):
        self._start_handler()
        self.handler.stop()
        self.worker.join(1)
        self.assertFalse(self.worker.is_alive())
        self.assertEqual(self.received, b"")
        self.assertEqual(termios.tcgetattr(self.slave_fd), self.original_attrs)

    def test_restore_does_not_overwrite_a_later_terminal_state(self):
        self._start_handler()
        self.handler.stop()
        self.worker.join(1)
        self.assertFalse(self.worker.is_alive())
        tty.setraw(self.slave_fd, termios.TCSANOW)
        later_attrs = termios.tcgetattr(self.slave_fd)
        self.handler.restore_terminal()
        self.assertEqual(termios.tcgetattr(self.slave_fd), later_attrs)
        self.handler.run()
        self.assertEqual(termios.tcgetattr(self.slave_fd), later_attrs)

    def test_stopped_before_start_does_not_change_terminal(self):
        later_attrs = termios.tcgetattr(self.slave_fd)
        later_attrs[3] ^= termios.ECHO
        termios.tcsetattr(self.slave_fd, termios.TCSANOW, later_attrs)
        self.handler.stop()
        with patch("ttyd_cli.platforms.unix.tty.setraw") as setraw:
            self.handler.run()
            setraw.assert_not_called()
        self.handler.restore_terminal()
        self.assertEqual(termios.tcgetattr(self.slave_fd), later_attrs)
        self.assertEqual(self.received, b"")

    def test_stop_during_start_restores_terminal(self):
        original_setraw = tty.setraw

        def stop_during_start(descriptor, when):
            original_setraw(descriptor, when)
            self.handler.stop()

        with (
            patch("ttyd_cli.platforms.unix.tty.setraw", side_effect=stop_during_start),
            patch.object(self.handler, "read_input") as read_input,
        ):
            self.handler.run()
            read_input.assert_not_called()
        self.assertEqual(termios.tcgetattr(self.slave_fd), self.original_attrs)
        self.assertEqual(self.received, b"")

    def test_input_read_during_stop_is_not_sent(self):
        def stop_after_read():
            self.handler.stop()
            return b"late input"

        with patch.object(self.handler, "read_input", side_effect=stop_after_read):
            self.handler.run()
        self.assertEqual(self.received, b"")
        self.assertEqual(termios.tcgetattr(self.slave_fd), self.original_attrs)

    def test_pipe_eof_exits(self):
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        try:
            pipe_stdin = Mock()
            pipe_stdin.fileno.return_value = read_fd
            callback = Mock()
            with patch("ttyd_cli.platforms.unix.sys.stdin", pipe_stdin):
                pipe_handler = UnixInputHandler(callback)
                pipe_handler.run()
            self.assertFalse(pipe_handler.connected)
            callback.assert_not_called()
        finally:
            os.close(read_fd)

    def test_terminal_eof_stops_and_restores(self):
        with (
            patch("ttyd_cli.platforms.unix.select.select", return_value=([self.slave_fd], [], [])),
            patch("ttyd_cli.platforms.unix.os.read", return_value=b""),
        ):
            self.handler.run()
        self.assertFalse(self.handler.connected)
        self.assertEqual(self.received, b"")
        self.assertEqual(termios.tcgetattr(self.slave_fd), self.original_attrs)

    def test_interrupted_select_can_retry(self):
        with (
            patch(
                "ttyd_cli.platforms.unix.select.select",
                side_effect=[InterruptedError(), ([self.slave_fd], [], [])],
            ),
            patch("ttyd_cli.platforms.unix.os.read", return_value=b"retry"),
        ):
            self.assertIsNone(self.handler.read_input())
            self.assertTrue(self.handler.connected)
            self.assertEqual(self.handler.read_input(), b"retry")

    def test_select_errors_stop_and_restore(self):
        for error in (OSError(errno.EBADF, "Bad descriptor"), ValueError("Closed descriptor")):
            with self.subTest(error=error):
                handler = UnixInputHandler(self._receive)
                with patch("ttyd_cli.platforms.unix.select.select", side_effect=error):
                    handler.run()
                self.assertFalse(handler.connected)
                self.assertEqual(termios.tcgetattr(self.slave_fd), self.original_attrs)
        self.assertEqual(self.received, b"")

    def test_read_error_stops_and_restores(self):
        with (
            patch("ttyd_cli.platforms.unix.select.select", return_value=([self.slave_fd], [], [])),
            patch(
                "ttyd_cli.platforms.unix.os.read",
                side_effect=OSError(errno.EIO, "Read failed"),
            ),
        ):
            self.handler.run()
        self.assertFalse(self.handler.connected)
        self.assertEqual(self.received, b"")
        self.assertEqual(termios.tcgetattr(self.slave_fd), self.original_attrs)

    def test_callback_failure_restores_terminal(self):
        self.handler.send_callback = Mock(side_effect=RuntimeError("Send failed"))
        with (
            patch.object(self.handler, "read_input", return_value=b"input"),
            self.assertRaisesRegex(RuntimeError, "Send failed"),
        ):
            self.handler.run()
        self.assertFalse(self.handler.connected)
        self.assertEqual(termios.tcgetattr(self.slave_fd), self.original_attrs)

    def test_duplicate_run_does_not_restore_an_active_session(self):
        self._start_handler()
        raw_attrs = termios.tcgetattr(self.slave_fd)
        self.handler.run()
        self.assertEqual(termios.tcgetattr(self.slave_fd), raw_attrs)
        self.assertTrue(self.worker.is_alive())


if __name__ == "__main__":
    unittest.main()
