from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def max_retries():
    value = Path("/app/retry.conf").read_text().strip()
    return int(value.split("=", 1)[1])


def fetch(url):
    try:
        with urlopen(url, timeout=1) as response:
            return response.read().decode()
    except (HTTPError, URLError, TimeoutError):
        return None


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if self.path != "/checkout":
            self.send_response(404)
            self.end_headers()
            return

        for _ in range(max_retries()):
            if fetch("http://primary:9000/price") is not None:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"checkout-ok:primary")
                return

        fallback = fetch("http://fallback:9000/price")
        if fallback is None:
            self.send_response(503)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"checkout-ok:{fallback}".encode())

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
