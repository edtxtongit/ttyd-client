"""HTTP Basic authentication and credential-free service discovery."""

import base64
import re
from urllib.parse import urlsplit, urlunsplit

import requests
from requests import Session

from .exceptions import AuthenticationRequired, InvalidAuthorization


def normalize_url(url: str) -> str:
    parsed = urlsplit(url)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    if scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Use an http(s)://host[:port][/base-path] URL")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("Do not embed credentials or fragments in the URL")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    path = parsed.path.rstrip("/")
    if path.endswith(("/ws", "/token")):
        path = path.rsplit("/", 1)[0]
    return urlunsplit((scheme, parsed.netloc, path, parsed.query, ""))


class WebPage(Session):
    def __init__(self, url: str, verify: bool | str = True, timeout: float = 10) -> None:
        super().__init__()
        self.verify = verify
        self.trust_env = False
        self.timeout = timeout
        self.url = normalize_url(url)
        parsed = urlsplit(self.url)
        self.token_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path + "/token", "", ""))

    @staticmethod
    def _token(response):
        if response.status_code == 401:
            challenge = response.headers.get("WWW-Authenticate", "")
            if not re.search(r"(?:^|,)\s*Basic\b", challenge, re.IGNORECASE):
                raise InvalidAuthorization("Server requires an unsupported authentication method")
            raise AuthenticationRequired("Credential Required")
        if response.status_code != 200:
            raise InvalidAuthorization(
                f"Token endpoint returned HTTP {response.status_code}; check the final URL and permissions"
            )
        try:
            token = response.json()["token"]
        except (ValueError, TypeError, KeyError) as error:
            raise InvalidAuthorization("Token endpoint did not return a ttyd token") from error
        if not isinstance(token, str):
            raise InvalidAuthorization("Token must be a string")
        return token or None

    def check(self) -> str | None:
        try:
            with self.get(self.token_url, timeout=self.timeout, allow_redirects=False) as response:
                return self._token(response)
        finally:
            self.close()

    def token(self, username: str, password: str) -> str | None:
        if not username or ":" in username:
            raise InvalidAuthorization("Username must be non-empty and cannot contain ':'")
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        self.headers["Authorization"] = f"Basic {encoded}"
        try:
            return self.check()
        except AuthenticationRequired as error:
            raise InvalidAuthorization("Credential Invalid") from error


def authenticate(
    url: str, credential: str | None = None, verify: bool | str = True, timeout: float = 10
) -> str | None:
    with WebPage(url, verify, timeout) as page:
        try:
            return page.check()
        except AuthenticationRequired:
            if not credential:
                raise
            if ":" not in credential:
                raise InvalidAuthorization(
                    "Credentials must use username:password format"
                ) from None
            username, password = credential.split(":", 1)
            return page.token(username, password)


def discover(url: str, verify: bool | str = True, timeout: float = 10) -> tuple[str, bool]:
    implicit = "://" not in url
    url = normalize_url("https://" + url if implicit else url)

    def probe(target):
        with WebPage(target, verify, timeout) as page:
            try:
                page.check()
                return target, False
            except AuthenticationRequired:
                return target, True

    try:
        return probe(url)
    except requests.exceptions.SSLError as error:
        if "CERTIFICATE_VERIFY_FAILED" in str(error):
            raise
        plaintext_hint = any(
            text in str(error) for text in ("WRONG_VERSION_NUMBER", "UNEXPECTED_EOF_WHILE_READING")
        )
        if not implicit or not plaintext_hint:
            raise
    except requests.exceptions.ConnectionError:
        if not implicit:
            raise
    parsed = urlsplit(url)
    return probe(urlunsplit(("http", parsed.netloc, parsed.path, parsed.query, "")))
