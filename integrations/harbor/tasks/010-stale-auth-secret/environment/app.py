from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def expected_token():
    for line in Path("/app/auth.env").read_text().splitlines():
        if line.startswith("EXPECTED_TOKEN="):
            return line.split("=", 1)[1]
    raise RuntimeError("EXPECTED_TOKEN is missing")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return

        if self.path != "/private":
            self.send_response(404)
            self.end_headers()
            return

        supplied = self.headers.get("Authorization", "")
        if supplied != f"Bearer {expected_token()}":
            self.send_response(401)
            self.end_headers()
            self.wfile.write(b"unauthorized")
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"private-data")

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


print("WARN cache refresh delayed; serving local cache", flush=True)
HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
