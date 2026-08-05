from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


def version():
    value = Path("/app/release.env").read_text().strip()
    return value.split("=", 1)[1]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        elif self.path == "/checkout" and version() == "v2":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"checkout-v2")
        elif self.path == "/checkout":
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"incompatible-checkout-schema")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
