"""CLI regression tests against the actual entry point."""

import unittest
from unittest.mock import Mock, patch

from ttyd_cli.__main__ import main, parse_arguments


class TestCLI(unittest.TestCase):
    @patch("ttyd_cli.__main__.authenticate")
    @patch("ttyd_cli.__main__.discover", return_value=("https://localhost", False))
    def test_check_does_not_open_shell(self, mock_discover, mock_auth):
        with patch("ttyd_cli.__main__.TTYDClient") as mock_client:
            self.assertEqual(main(["--url", "localhost", "--check"]), 0)
        mock_client.assert_not_called()
        mock_auth.assert_called_once()

    @patch("ttyd_cli.__main__.authenticate")
    @patch("ttyd_cli.__main__.discover", return_value=("https://localhost", True))
    @patch("ttyd_cli.__main__.getpass.getpass", return_value="secret")
    def test_hidden_password_prompt(self, mock_password, mock_discover, mock_auth):
        with patch("sys.stdin.isatty", return_value=True):
            self.assertEqual(main(["--url", "https://localhost", "-u", "alice", "--check"]), 0)
        mock_auth.assert_called_once_with("https://localhost", "alice:secret", True, timeout=10)

    @patch("ttyd_cli.__main__.discover", return_value=("http://localhost", True))
    def test_plaintext_auth_is_refused_noninteractive(self, mock_discover):
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch("ttyd_cli.__main__.authenticate") as mock_auth,
        ):
            self.assertEqual(
                main(["--url", "http://localhost", "--check", "--credential", "alice:secret"]), 1
            )
        mock_auth.assert_not_called()

    @patch("ttyd_cli.__main__.authenticate")
    @patch("ttyd_cli.__main__.discover", return_value=("http://localhost", True))
    def test_password_environment(self, mock_discover, mock_auth):
        with (
            patch("sys.stdin.isatty", return_value=False),
            patch.dict("os.environ", {"DEMO_PASS": "secret"}),
        ):
            result = main(
                [
                    "--url",
                    "http://localhost",
                    "--check",
                    "--allow-http-auth",
                    "-u",
                    "alice",
                    "--password-env",
                    "DEMO_PASS",
                ]
            )
        self.assertEqual(result, 0)
        mock_auth.assert_called_once_with("http://localhost", "alice:secret", True, timeout=10)

    @patch("ttyd_cli.__main__.TTYDClient")
    @patch("ttyd_cli.__main__.discover", return_value=("https://localhost", False))
    def test_interactive(self, mock_discover, mock_client):
        mock_client.return_value = Mock(_connection_established=True)
        mock_client.return_value.run_forever.return_value = False
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            self.assertEqual(main(["--url", "https://localhost", "--cmd", "ls"]), 0)
        self.assertEqual(mock_client.call_args.kwargs["cmd"], "ls")

    @patch("ttyd_cli.__main__.TTYDClient")
    @patch("ttyd_cli.__main__.discover", return_value=("https://localhost", False))
    def test_websocket_failure_returns_nonzero(self, mock_discover, mock_client):
        mock_client.return_value.run_forever.return_value = True
        with (
            patch("sys.stdin.isatty", return_value=True),
            patch("sys.stdout.isatty", return_value=True),
        ):
            self.assertEqual(main(["--url", "https://localhost"]), 1)

    def test_remote_args(self):
        self.assertEqual(
            parse_arguments(["--url", "http://localhost", "--args", "bash", "--login"]).args,
            ["bash", "--login"],
        )

    def test_invalid_timeout(self):
        for timeout in ("0", "-1", "nan", "inf"):
            with self.subTest(timeout=timeout), self.assertRaises(SystemExit):
                parse_arguments(["--url", "https://localhost", "--timeout", timeout])

    def test_conflicting_certificate_options(self):
        with self.assertRaises(SystemExit):
            parse_arguments(["--url", "https://localhost", "--no-verify", "--ca-file", "ca.pem"])
