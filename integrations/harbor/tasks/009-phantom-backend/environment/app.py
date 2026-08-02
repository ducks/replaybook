import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

BACKEND = "http://backend:9000/ping"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        try:
            urllib.request.urlopen(BACKEND, timeout=2)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        except Exception as error:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(f"backend error: {error}".encode())
            print(f"backend error: {error}", flush=True)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("app listening on :8080", flush=True)
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
