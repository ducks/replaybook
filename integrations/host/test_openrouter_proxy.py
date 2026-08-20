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


SCRIPT = Path(__file__).with_name("openrouter_proxy.py")


class UpstreamHandler(http.server.BaseHTTPRequestHandler):
    authorization = ""
    request_path = ""

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        type(self).authorization = self.headers.get("Authorization", "")
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
