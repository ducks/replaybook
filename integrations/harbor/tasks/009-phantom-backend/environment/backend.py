from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"pong")

    def log_message(self, fmt, *args):
        print(f"backend: {self.address_string()} {fmt % args}", flush=True)


if __name__ == "__main__":
    print("backend listening on :9000", flush=True)
    HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
