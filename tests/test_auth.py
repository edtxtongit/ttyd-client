"""Authentication and TLS discovery regression tests."""

import json
import unittest
from unittest.mock import patch

import requests

from ttyd_cli.auth import authenticate, discover, normalize_url
from ttyd_cli.exceptions import AuthenticationRequired, InvalidAuthorization


def response(status=200, payload=None, challenge=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps({"token": ""} if payload is None else payload).encode()
    result._content_consumed = True
    if challenge:
        result.headers["WWW-Authenticate"] = challenge
    return result


class TestAuthentication(unittest.TestCase):
    @patch("ttyd_cli.auth.Session.get")
    def test_no_auth_fetches_token_without_credentials(self, mock_get):
        mock_get.return_value = response()
        self.assertIsNone(authenticate("http://localhost:7681/"))
        mock_get.assert_called_once_with(
            "http://localhost:7681/token", timeout=10, allow_redirects=False
        )

    @patch("ttyd_cli.auth.Session.get")
    def test_basic_login(self, mock_get):
        mock_get.side_effect = [
            response(401, challenge='Basic realm="ttyd"'),
            response(payload={"token": "dXNlcjpwYXNz"}),
        ]
        self.assertEqual(authenticate("https://localhost/base/", "user:pass"), "dXNlcjpwYXNz")
        self.assertEqual(mock_get.call_count, 2)

    @patch("ttyd_cli.auth.Session.get")
    def test_required_login(self, mock_get):
        mock_get.return_value = response(401, challenge="Basic realm=ttyd")
        with self.assertRaises(AuthenticationRequired):
            authenticate("http://localhost")

    @patch("ttyd_cli.auth.Session.get")
    def test_bad_credentials(self, mock_get):
        mock_get.side_effect = [
            response(401, challenge="Basic realm=ttyd"),
            response(401, challenge="Basic realm=ttyd"),
        ]
        with self.assertRaisesRegex(InvalidAuthorization, "Credential Invalid"):
            authenticate("http://localhost", "user:wrong")

    @patch("ttyd_cli.auth.Session.get")
    def test_unsupported_auth_does_not_send_credentials(self, mock_get):
        mock_get.return_value = response(401, challenge="Bearer")
        with self.assertRaises(InvalidAuthorization):
            authenticate("http://localhost", "user:pass")
        self.assertEqual(mock_get.call_count, 1)

    @patch("ttyd_cli.auth.Session.get")
    def test_errors_do_not_trigger_login(self, mock_get):
        for status in (302, 403, 407, 404, 500):
            with self.subTest(status=status):
                mock_get.reset_mock()
                mock_get.return_value = response(status)
                with self.assertRaises(InvalidAuthorization):
                    authenticate("http://localhost", "user:pass")
                self.assertEqual(mock_get.call_count, 1)

    @patch("ttyd_cli.auth.Session.get")
    def test_invalid_token(self, mock_get):
        for payload in ({}, {"token": None}, {"token": 123}, []):
            with self.subTest(payload=payload):
                mock_get.return_value = response(payload=payload)
                with self.assertRaises(InvalidAuthorization):
                    authenticate("http://localhost")

    @patch("ttyd_cli.auth.Session.get")
    def test_malformed_credential(self, mock_get):
        mock_get.return_value = response(401, challenge="Basic")
        with self.assertRaises(InvalidAuthorization):
            authenticate("http://localhost", "missing-colon")

    @patch("ttyd_cli.auth.WebPage.check", return_value=None)
    def test_explicit_https_never_falls_back(self, mock_check):
        self.assertEqual(discover("https://localhost:7681"), ("https://localhost:7681", False))
        mock_check.side_effect = requests.exceptions.SSLError("WRONG_VERSION_NUMBER")
        with self.assertRaises(requests.exceptions.SSLError):
            discover("https://localhost:7681")

    @patch("ttyd_cli.auth.WebPage.check")
    def test_bare_host_plaintext_discovery(self, mock_check):
        mock_check.side_effect = [
            requests.exceptions.SSLError("WRONG_VERSION_NUMBER"),
            AuthenticationRequired(),
        ]
        self.assertEqual(discover("localhost:7681"), ("http://localhost:7681", True))

    @patch("ttyd_cli.auth.WebPage.check")
    def test_bad_certificate_never_downgrades(self, mock_check):
        mock_check.side_effect = requests.exceptions.SSLError("CERTIFICATE_VERIFY_FAILED")
        with self.assertRaises(requests.exceptions.SSLError):
            discover("localhost:7681")
        self.assertEqual(mock_check.call_count, 1)

    def test_url_normalization(self):
        self.assertEqual(
            normalize_url("wss://localhost/base/ws?arg=hello"), "https://localhost/base?arg=hello"
        )
        for url in (
            "http://user:pass@localhost",
            "http://localhost/#fragment",
            "ftp://localhost",
            "http://localhost:bad",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                normalize_url(url)
