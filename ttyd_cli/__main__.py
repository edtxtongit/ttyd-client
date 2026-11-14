"""Command-line interface for TTYD client."""

import argparse
import getpass
import math
import os
import sys
from urllib.parse import urlsplit

import requests

from .auth import authenticate, discover
from .client import TTYDClient
from .exceptions import InvalidAuthorization


def positive_timeout(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Timeout must be a positive finite number")
    return number


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description="TTYD WebSocket Terminal Client")
    parser.add_argument(
        "--url",
        required=True,
        help="http(s)://host[:port][/base-path], or host:port for TLS discovery",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Testing only: disable HTTP and WebSocket TLS verification",
    )
    parser.add_argument("--ca-file", help="Custom CA certificate PEM file")
    parser.add_argument(
        "--credential", help="Legacy username:password option; prefer hidden password input"
    )
    parser.add_argument("-u", "--username", help="Basic username; prompt when omitted")
    parser.add_argument(
        "--password-env", metavar="NAME", help="Read password from this environment variable"
    )
    parser.add_argument(
        "--allow-http-auth",
        action="store_true",
        help="Explicitly allow sending Basic credentials over plaintext HTTP",
    )
    parser.add_argument(
        "--timeout",
        type=positive_timeout,
        default=10,
        help="HTTP discovery/auth timeout in seconds",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check TLS/authentication without opening a remote terminal",
    )
    parser.add_argument("-c", "--cmd", default="", help="Command to execute on connection")
    parser.add_argument(
        "--args",
        dest="remote_args",
        nargs=argparse.REMAINDER,
        default=None,
        help="Remaining arguments to pass to the remote command",
    )
    parser.add_argument("args", nargs="*", help="Remote command arguments")
    arguments = parser.parse_args(argv)
    if arguments.remote_args is not None:
        arguments.args.extend(arguments.remote_args)
    if arguments.ca_file and arguments.no_verify:
        parser.error("--ca-file and --no-verify cannot be used together")
    if arguments.credential and (arguments.username or arguments.password_env):
        parser.error("--credential cannot be combined with --username or --password-env")
    return arguments


def login_credential(arguments, secure):
    if (
        not secure
        and not arguments.allow_http_auth
        and (
            not sys.stdin.isatty()
            or input("HTTP sends credentials in plaintext. Continue? Type yes: ").strip().lower()
            != "yes"
        )
    ):
        raise InvalidAuthorization(
            "Plaintext login cancelled; use HTTPS or explicitly allow HTTP auth"
        )
    if arguments.credential:
        print(
            "Warning: --credential exposes the password in process arguments; prefer hidden input.",
            file=sys.stderr,
        )
        return arguments.credential
    if not sys.stdin.isatty() and (not arguments.username or not arguments.password_env):
        raise InvalidAuthorization("Non-interactive login requires --username and --password-env")
    username = arguments.username or input("Username: ").strip()
    if arguments.password_env:
        if arguments.password_env not in os.environ:
            raise InvalidAuthorization(f"Environment variable {arguments.password_env} is not set")
        password = os.environ[arguments.password_env]
    else:
        password = getpass.getpass("Password: ")
    return f"{username}:{password}"


def main(argv=None) -> int:
    stage = "Service discovery"
    try:
        arguments = parse_arguments(argv)
        if not arguments.check and (not sys.stdin.isatty() or not sys.stdout.isatty()):
            raise InvalidAuthorization(
                "Interactive mode needs a real terminal; use --check for discovery"
            )
        verify = False if arguments.no_verify else (arguments.ca_file or True)
        url, required = discover(arguments.url.strip(), verify=verify, timeout=arguments.timeout)
        secure = urlsplit(url).scheme == "https"
        print(f"TLS: {'enabled' if secure else 'disabled (plaintext HTTP/WS)'}", file=sys.stderr)
        if secure:
            print(
                f"Certificate verification: {'disabled (testing only)' if verify is False else 'enabled'}",
                file=sys.stderr,
            )
        print(
            f"Authentication: {'HTTP Basic required' if required else 'no HTTP Basic challenge'}",
            file=sys.stderr,
        )
        credential = login_credential(arguments, secure) if required else None
        stage = "HTTP authentication (/token)"
        if arguments.check:
            authenticate(url, credential, verify, timeout=arguments.timeout)
            print("Check passed; no remote terminal was started.", file=sys.stderr)
            return 0
        client = TTYDClient(
            url=url,
            credential=credential,
            args=arguments.args,
            cmd=arguments.cmd,
            verify=verify,
            timeout=arguments.timeout,
        )
        stage = "WebSocket connection (/ws)"
        failed = client.run_forever()
        if failed or not client._connection_established:
            raise InvalidAuthorization(
                f"WebSocket connection failed: {client.last_error or 'no terminal session established'}"
            )
        return 0
    except KeyboardInterrupt:
        return 130
    except EOFError:
        print("[!] Login input ended", file=sys.stderr)
        return 1
    except requests.RequestException as error:
        print(f"[!] {stage} failed: {error}", file=sys.stderr)
        return 1
    except (InvalidAuthorization, OSError, ValueError) as error:
        print(f"[!] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
