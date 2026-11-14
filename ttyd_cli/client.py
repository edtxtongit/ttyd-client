"""TTYD WebSocket client implementation."""

from __future__ import annotations

import base64
import codecs
import json
import ssl
import sys
from functools import partial
from threading import Event, Thread, current_thread
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import websocket

from .auth import authenticate, normalize_url
from .output import TerminalOutputFilter
from .platforms import InputHandler
from .utils import IS_WINDOWS, get_terminal_size

if not IS_WINDOWS:
    from signal import SIGWINCH, signal


class TTYDClient(websocket.WebSocketApp):
    """WebSocket terminal client with interruptible input and incremental output."""

    @property
    def sock(self):
        return self._socket

    @sock.setter
    def sock(self, connection):
        """Disable automatic redirects on each socket created by WebSocketApp."""
        self._socket = connection
        if connection is not None:
            connection.connect = partial(connection.connect, redirect_limit=0)

    def __init__(
        self,
        url: str,
        credential: str | None = None,
        args: list[str] | None = None,
        cmd: str = "",
        verify: bool | str = True,
        timeout: float = 10,
    ) -> None:
        url = normalize_url(url)
        token = authenticate(url, credential, verify, timeout=timeout)
        parsed = urlsplit(url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        query.extend(("arg", argument) for argument in (args or []))
        ws_url = urlunsplit(
            (
                "wss" if parsed.scheme == "https" else "ws",
                parsed.netloc,
                parsed.path + "/ws",
                urlencode(query),
                "",
            )
        )
        headers = []
        if token and credential:
            encoded = base64.b64encode(credential.encode("utf-8")).decode("ascii")
            headers.append(f"Authorization: Basic {encoded}")
        super().__init__(
            ws_url,
            header=headers,
            subprotocols=["tty"],
            on_open=self._on_open,
            on_message=self._on_message,
            on_close=self._on_close,
            on_error=self._on_error,
        )
        self.credential = token
        self.cmd = cmd
        self.connected = False
        self._connection_established = False
        self._input_handler = None
        self._input_thread = None
        self._resize_thread = None
        self._resize_stop = Event()
        self._last_size = None
        self._resize_monitor_running = False
        self._previous_resize_handler = None
        self._resize_handler_installed = False
        self._output_filter = TerminalOutputFilter()
        self._output_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._output_finished = False
        self._verify = verify
        self._origin = f"{parsed.scheme}://{parsed.netloc}"
        self._hostname = parsed.hostname
        self.last_error = None

    def _filter_output(self, data: bytes) -> bytes:
        return self._output_filter.feed(data)

    def _write_output(self, data: bytes, final: bool = False) -> None:
        text = self._output_decoder.decode(data, final=final)
        if text:
            sys.stdout.write(text)
            sys.stdout.flush()

    def _stop_io(self) -> None:
        self.connected = False
        self._resize_monitor_running = False
        self._resize_stop.set()
        if self._input_handler:
            self._input_handler.stop()
        for thread in (self._input_thread, self._resize_thread):
            if thread is not None and thread is not current_thread():
                thread.join(timeout=2)
        if self._input_handler and (
            self._input_thread is None or not self._input_thread.is_alive()
        ):
            self._input_handler.restore_terminal()
        if self._resize_handler_installed:
            try:
                signal(SIGWINCH, self._previous_resize_handler)
                self._resize_handler_installed = False
            except ValueError:
                pass

    def _on_close(self, ws: websocket.WebSocketApp, code: int | None, msg: str | None) -> None:
        self._stop_io()
        if not self._output_finished:
            self._write_output(self._output_filter.flush(), final=True)
            self._output_finished = True

    def _on_error(self, ws: websocket.WebSocketApp, error: Exception) -> None:
        self.last_error = error

    def _on_message(self, ws: websocket.WebSocketApp, msg: bytes | str) -> None:
        if not msg:
            return
        if isinstance(msg, str):
            msg = msg.encode("utf-8")
        if not self.connected:
            self.connected = True
            self._connection_established = True
            self._input_handler = InputHandler(self._send_command)
            self._input_thread = Thread(target=self._input_handler.run, daemon=True)
            self._input_thread.start()
            if IS_WINDOWS:
                self._start_windows_resize_monitor()
            if self.cmd:
                self._send_command(self.cmd + "\n")
        if msg[:1] == b"0":
            self._write_output(self._filter_output(msg[1:]))

    def _resize(self, *arguments) -> None:
        columns, rows = get_terminal_size()
        self.send(
            b"1" + json.dumps({"columns": columns, "rows": rows}).encode("utf-8"),
            opcode=websocket.ABNF.OPCODE_BINARY,
        )

    def _send_command(self, command: str | bytes) -> None:
        if not self.connected:
            return
        if isinstance(command, str):
            command = command.replace("\n", "\r").encode("utf-8")
        self.send(b"0" + command, opcode=websocket.ABNF.OPCODE_BINARY)

    def _start_windows_resize_monitor(self) -> None:
        if not IS_WINDOWS or not self.connected:
            return
        if self._resize_thread is not None and self._resize_thread.is_alive():
            return
        self._last_size = get_terminal_size()
        self._resize_stop.clear()
        self._resize_monitor_running = True

        def monitor_resize():
            try:
                while not self._resize_stop.wait(0.25):
                    if not self.connected:
                        break
                    size = get_terminal_size()
                    if size != self._last_size:
                        self._last_size = size
                        self._resize()
            finally:
                self._resize_monitor_running = False

        self._resize_thread = Thread(target=monitor_resize, daemon=True)
        self._resize_thread.start()

    def _on_open(self, ws: websocket.WebSocketApp) -> None:
        if self._input_thread is not None and self._input_thread.is_alive():
            raise RuntimeError("Previous terminal input thread is still running")
        self._output_filter = TerminalOutputFilter()
        self._output_decoder.reset()
        self._output_finished = False
        self.connected = False
        self._connection_established = False
        self.last_error = None
        columns, rows = get_terminal_size()
        message = {"AuthToken": self.credential or "", "columns": columns, "rows": rows}
        self.send(json.dumps(message).encode("utf-8"), opcode=websocket.ABNF.OPCODE_BINARY)
        if not IS_WINDOWS:
            try:
                self._previous_resize_handler = signal(SIGWINCH, self._resize)
                self._resize_handler_installed = True
            except ValueError:
                pass

    def run_forever(self, **options):
        ssl_options = dict(options.pop("sslopt", None) or {})
        if self._verify is False:
            ssl_options.setdefault("cert_reqs", ssl.CERT_NONE)
            ssl_options.setdefault("check_hostname", False)
        elif isinstance(self._verify, str):
            ssl_options.setdefault("ca_certs", self._verify)
            ssl_options.setdefault("cert_reqs", ssl.CERT_REQUIRED)
        options.setdefault("origin", self._origin)
        options.setdefault("http_no_proxy", [self._hostname])
        try:
            return super().run_forever(sslopt=ssl_options, **options)
        finally:
            self._on_close(self, None, None)
