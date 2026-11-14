"""Optional end-to-end tests against a real ttyd binary and POSIX PTY."""

import contextlib
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

if os.name == "posix":
    import fcntl
    import pty
    import select
    import signal
    import struct
    import termios


TTYD_BINARY = os.environ.get("TTYD_BINARY") or shutil.which("ttyd")


@unittest.skipUnless(
    os.name == "posix" and TTYD_BINARY and shutil.which("openssl"),
    "Requires POSIX, ttyd and openssl",
)
class TestRealTTYD(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(os.environ.get("TTYD_TEST_TMPDIR", tempfile.gettempdir()))
        cls.temporary = tempfile.TemporaryDirectory(prefix="ttyd-client-tests-", dir=root)
        cls.certificate = Path(cls.temporary.name) / "cert.pem"
        cls.key = Path(cls.temporary.name) / "key.pem"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-keyout",
                str(cls.key),
                "-out",
                str(cls.certificate),
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=IP:127.0.0.1,DNS:localhost",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @contextlib.contextmanager
    def server(self, tls=False, authenticated=False):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        command = [TTYD_BINARY, "-i", "127.0.0.1", "-p", str(port), "-W", "-b", "/terminal"]
        if authenticated:
            command.extend(["-c", "alice:cloud-test-secret"])
        if tls:
            command.extend(["-S", "-C", str(self.certificate), "-K", str(self.key)])
        process = subprocess.Popen(
            command + ["sh", "-c", "printf '__READY__\\n'; PS1='' PS2='' exec sh"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.server_process = process
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    self.fail("ttyd failed to start")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.02)
            else:
                self.fail("ttyd startup timed out")
            yield f"{'https' if tls else 'http'}://127.0.0.1:{port}/terminal"
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    @staticmethod
    def environment():
        environment = os.environ.copy()
        for name in tuple(environment):
            if name.lower() in ("http_proxy", "https_proxy", "all_proxy"):
                environment.pop(name)
        return environment

    def interact(
        self, url, options=(), authenticated=False, plaintext=False, unexpected_disconnect=False
    ):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        saved = termios.tcgetattr(slave)
        process = subprocess.Popen(
            [sys.executable, "-m", "ttyd_cli", "--url", url, *options],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=self.environment(),
            start_new_session=True,
        )
        output = bytearray()

        def wait_for(expected):
            deadline = time.monotonic() + 8
            while expected not in output and time.monotonic() < deadline:
                readable, _, _ = select.select([master], [], [], 0.1)
                if readable:
                    output.extend(os.read(master, 4096))
                if process.poll() is not None:
                    break
            self.assertIn(expected, output, output.decode(errors="replace"))

        try:
            if authenticated:
                if plaintext:
                    wait_for(b"Type yes:")
                    os.write(master, b"yes\n")
                wait_for(b"Username:")
                os.write(master, b"alice\n")
                wait_for(b"Password:")
                os.write(master, b"cloud-test-secret\n")
            wait_for(b"__READY__")
            deadline = time.monotonic() + 2
            while termios.tcgetattr(slave)[3] & termios.ICANON and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(termios.tcgetattr(slave)[3] & termios.ICANON)
            os.write(master, "printf '中文\\n'\r".encode())
            wait_for("\r\n中文\r\n".encode())
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
            os.kill(process.pid, signal.SIGWINCH)
            time.sleep(0.1)
            os.write(master, b"stty size\r")
            wait_for(b"30 100")
            os.write(master, b"sleep 20\r")
            wait_for(b"sleep 20")
            time.sleep(0.1)
            os.write(master, b"\x03")
            os.write(master, b"printf '__CTRL_OK__\\n'\r")
            wait_for(b"\r\n__CTRL_OK__\r\n")
            if unexpected_disconnect:
                self.server_process.kill()
                self.assertIn(process.wait(timeout=6), (0, 1))
            else:
                os.write(master, b"exit\r")
                self.assertEqual(process.wait(timeout=6), 0)
            self.assertEqual(termios.tcgetattr(slave), saved)
            self.assertNotIn(b"cloud-test-secret", output)
            self.assertNotIn("�".encode(), output)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
            os.close(slave)

    def test_plain_terminal(self):
        with self.server() as url:
            self.interact(url)

    def test_unexpected_disconnect_restores_terminal(self):
        with self.server() as url:
            self.interact(url, unexpected_disconnect=True)

    def test_basic_prompt_and_terminal(self):
        with self.server(authenticated=True) as url:
            self.interact(url, authenticated=True, plaintext=True)

    def test_bare_host_discovery(self):
        with self.server() as url:
            self.interact(url.removeprefix("http://"))

    def test_self_signed_no_verify_terminal(self):
        with self.server(tls=True, authenticated=True) as url:
            self.interact(url, options=("--no-verify",), authenticated=True)

    def test_custom_ca_terminal(self):
        with self.server(tls=True, authenticated=True) as url:
            self.interact(url, options=("--ca-file", str(self.certificate)), authenticated=True)

    def test_bad_certificate_fails(self):
        with self.server(tls=True) as url:
            result = subprocess.run(
                [sys.executable, "-m", "ttyd_cli", "--url", url, "--check"],
                env=self.environment(),
                capture_output=True,
                timeout=8,
                check=False,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"CERTIFICATE_VERIFY_FAILED", result.stderr)
