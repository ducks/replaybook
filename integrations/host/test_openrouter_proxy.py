from __future__ import annotations

import http.server
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from integrations.host.openrouter_proxy import OpenRouterProxy


SCRIPT = Path(__file__).with_name("openrouter_proxy.py")


class UpstreamHandler(http.server.BaseHTTPRequestHandler):
    authorization = ""
    api_key = ""
    request_path = ""

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        type(self).authorization = self.headers.get("Authorization", "")
        type(self).api_key = self.headers.get("X-Api-Key", "")
        type(self).request_path = self.path
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProxyTests(unittest.TestCase):
    def test_forwards_small_sse_event_before_upstream_finishes(self) -> None:
        release = threading.Event()
        event = b'data: {"choices":[]}\n\n'

        class StreamingHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("content-length", "0")))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(event)
                self.wfile.flush()
                release.wait(timeout=5)
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StreamingHandler)
        proxy = OpenRouterProxy(
            ("127.0.0.1", 0), "host-secret",
            f"http://127.0.0.1:{upstream.server_address[1]}",
        )
        for server in (upstream, proxy):
            threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            request = urllib.request.Request(
                f"http://127.0.0.1:{proxy.server_address[1]}/v1/chat/completions",
                data=b'{}',
            )
            with urllib.request.urlopen(request, timeout=1) as response:
                # The upstream is deliberately still open. Waiting for a full
                # buffer here would hide live tokens and trigger idle timeouts.
                self.assertEqual(response.readline(), event.splitlines(keepends=True)[0])
                self.assertFalse(release.is_set())
                release.set()
                self.assertEqual(response.read(), b"\ndata: [DONE]\n\n")
        finally:
            release.set()
            for server in (proxy, upstream):
                server.shutdown()
                server.server_close()

    def test_injects_host_credential_without_accepting_vm_credential(self) -> None:
        upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / "ready"
            environment = dict(os.environ)
            environment["REPLAYBOOK_OPENAI_API_KEY"] = "host-secret"
            environment["REPLAYBOOK_OPENAI_UPSTREAM"] = (
                f"http://127.0.0.1:{upstream.server_address[1]}/zen/go"
            )
            process = subprocess.Popen(
                [
                    "python",
                    str(SCRIPT),
                    "--port",
                    "0",
                    "--ready-file",
                    str(ready),
                ],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                for _ in range(100):
                    if ready.exists():
                        break
                    self.assertIsNone(process.poll())
                    time.sleep(0.02)
                port = int(ready.read_text())
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    data=json.dumps({"model": "test"}).encode(),
                    headers={"Authorization": "Bearer vm-placeholder"},
                )
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(json.load(response), {"ok": True})
                self.assertEqual(UpstreamHandler.authorization, "Bearer host-secret")
                self.assertEqual(UpstreamHandler.api_key, "host-secret")
                self.assertEqual(
                    UpstreamHandler.request_path, "/zen/go/v1/chat/completions"
                )
            finally:
                process.terminate()
                process.wait(timeout=5)
                upstream.shutdown()
                upstream.server_close()
            stdout, stderr = process.communicate()
            self.assertNotIn("host-secret", stdout)
            self.assertNotIn("host-secret", stderr)


if __name__ == "__main__":
    unittest.main()
