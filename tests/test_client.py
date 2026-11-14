"""WebSocket protocol, output, TLS and lifecycle regression tests."""

import io
import json
import ssl
import unittest
from unittest.mock import Mock, patch

import websocket

from ttyd_cli.client import TTYDClient


class TestTTYDClient(unittest.TestCase):
    @patch("ttyd_cli.client.authenticate", return_value="test_token")
    def setUp(self, mock_auth):
        self.client = TTYDClient("http://localhost:7681", "user:pass", verify=False)

    def test_initialization(self):
        self.assertEqual(self.client.credential, "test_token")
        self.assertFalse(self.client.connected)
        self.assertEqual(self.client.subprotocols, ["tty"])
        self.assertEqual(self.client.header, ["Authorization: Basic dXNlcjpwYXNz"])

    def test_websocket_redirects_are_disabled(self):
        connection = Mock()
        original_connect = connection.connect
        self.client.sock = connection
        self.client.sock.connect("wss://localhost/ws", header=self.client.header)
        original_connect.assert_called_once_with(
            "wss://localhost/ws", header=self.client.header, redirect_limit=0
        )
        self.client.sock = None

    @patch("ttyd_cli.client.authenticate", return_value=None)
    def test_url_and_argument_encoding(self, mock_auth):
        client = TTYDClient("https://localhost/base/?arg=first", args=["second arg", "third&arg"])
        self.assertEqual(
            client.url, "wss://localhost/base/ws?arg=first&arg=second+arg&arg=third%26arg"
        )
        self.assertEqual(client.header, [])

    def test_multiframe_utf8(self):
        self.client.connected = True
        encoded = "中文".encode()
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            for byte in encoded:
                self.client._on_message(self.client, b"0" + bytes([byte]))
            self.assertEqual(output.getvalue(), "中文")

    def test_empty_and_control_messages(self):
        self.client.connected = True
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            for message in (b"", b"1title", b"2{}", b"0"):
                self.client._on_message(self.client, message)
            self.assertEqual(output.getvalue(), "")

    def test_close_flushes_pending_output(self):
        self.client.connected = True
        with patch("sys.stdout", new_callable=io.StringIO) as output:
            self.client._on_message(self.client, b"0text\x1b")
            self.client._on_close(self.client, 1000, "")
            self.client._on_close(self.client, 1000, "")
            self.assertEqual(output.getvalue(), "text\x1b")

    def test_close_stops_and_joins_before_restore(self):
        handler = Mock()
        thread = Mock()
        thread.is_alive.return_value = False
        manager = Mock()
        manager.attach_mock(handler, "handler")
        manager.attach_mock(thread, "thread")
        self.client._input_handler = handler
        self.client._input_thread = thread
        self.client._on_close(self.client, 1000, "")
        names = [call[0] for call in manager.mock_calls]
        self.assertLess(names.index("handler.stop"), names.index("thread.join"))
        self.assertLess(names.index("thread.join"), names.index("handler.restore_terminal"))

    def test_send_command_preserves_bytes(self):
        self.client.connected = True
        with patch.object(self.client, "send") as send:
            self.client._send_command(b"\x03\x04\x1b[A\xff")
        send.assert_called_once_with(b"0\x03\x04\x1b[A\xff", opcode=websocket.ABNF.OPCODE_BINARY)

    def test_send_command_encodes_text_and_command_newlines(self):
        self.client.connected = True
        with patch.object(self.client, "send") as send:
            self.client._send_command("echo 中文\n")
        send.assert_called_once_with("0echo 中文\r".encode(), opcode=websocket.ABNF.OPCODE_BINARY)

    def test_disconnected_input_is_not_sent(self):
        with patch.object(self.client, "send") as send:
            self.client._send_command("late input")
        send.assert_not_called()

    @patch("ttyd_cli.client.get_terminal_size", return_value=(80, 24))
    def test_on_open_initializes_token_and_dimensions(self, mock_size):
        with patch.object(self.client, "send") as send:
            self.client._on_open(self.client)
        payload = json.loads(send.call_args.args[0])
        self.assertEqual(payload, {"AuthToken": "test_token", "columns": 80, "rows": 24})
        self.assertEqual(send.call_count, 1)
        self.client._on_close(self.client, 1000, "")

    @patch("ttyd_cli.client.InputHandler")
    @patch("ttyd_cli.client.Thread")
    def test_first_message_starts_input_once(self, mock_thread, mock_handler):
        self.client.cmd = "ls"
        with patch.object(self.client, "_send_command") as send:
            self.client._on_message(self.client, b"1title")
            self.client._on_message(self.client, b"2{}")
        mock_handler.assert_called_once()
        mock_thread.return_value.start.assert_called_once()
        send.assert_called_once_with("ls\n")

    @patch("ttyd_cli.client.IS_WINDOWS", True)
    @patch("ttyd_cli.client.get_terminal_size")
    @patch("ttyd_cli.client.Thread")
    def test_windows_resize_monitor_starts_after_connected(self, mock_thread, mock_size):
        self.client._start_windows_resize_monitor()
        mock_thread.assert_not_called()
        self.client.connected = True
        mock_size.side_effect = [(80, 24), (100, 30)]
        with (
            patch.object(self.client._resize_stop, "wait", side_effect=[False, True]),
            patch.object(self.client, "_resize") as resize,
        ):
            mock_thread.return_value.start.side_effect = lambda: mock_thread.call_args.kwargs[
                "target"
            ]()
            self.client._start_windows_resize_monitor()
        resize.assert_called_once()

    @patch("websocket.WebSocketApp.run_forever", return_value=False)
    def test_no_verify_applies_to_websocket(self, run):
        self.client.run_forever()
        options = run.call_args.kwargs
        self.assertEqual(options["sslopt"]["cert_reqs"], ssl.CERT_NONE)
        self.assertFalse(options["sslopt"]["check_hostname"])
        self.assertEqual(options["origin"], "http://localhost:7681")

    @patch("websocket.WebSocketApp.run_forever", return_value=False)
    def test_custom_ca(self, run):
        self.client._verify = "/workspace/custom-ca.pem"
        self.client.run_forever()
        self.assertEqual(run.call_args.kwargs["sslopt"]["ca_certs"], "/workspace/custom-ca.pem")

    @patch("websocket.WebSocketApp.run_forever", return_value=False)
    def test_default_keeps_verification_enabled(self, run):
        self.client._verify = True
        self.client.run_forever()
        self.assertNotIn("cert_reqs", run.call_args.kwargs["sslopt"])
