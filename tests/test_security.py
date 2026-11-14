"""Ensure WebSocket redirects cannot forward credentials to another server."""

import base64
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ttyd_cli.client import TTYDClient


class TestWebSocketRedirects(unittest.TestCase):
    def test_redirect_is_refused_before_contacting_target(self):
        target_requests = []
        encoded = base64.b64encode(b"alice:secret").decode()

        class TargetHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                target_requests.append(dict(self.headers))
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, message, *arguments):
                return

        target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)

        class SourceHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Authorization") != f"Basic {encoded}":
                    self.send_response(401)
                    self.send_header("WWW-Authenticate", 'Basic realm="ttyd"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif self.path == "/token":
                    body = json.dumps({"token": encoded}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(302)
                    self.send_header("Location", f"ws://127.0.0.1:{target.server_port}/ws")
                    self.send_header("Content-Length", "0")
                    self.end_headers()

            def log_message(self, message, *arguments):
                return

        source = ThreadingHTTPServer(("127.0.0.1", 0), SourceHandler)
        workers = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (source, target)
        ]
        for worker in workers:
            worker.start()
        try:
            client = TTYDClient(f"http://127.0.0.1:{source.server_port}", "alice:secret")
            self.assertTrue(client.run_forever())
            self.assertFalse(client._connection_established)
            self.assertEqual(target_requests, [])
        finally:
            for server in (source, target):
                server.shutdown()
                server.server_close()
            for worker in workers:
                worker.join(timeout=2)
