import os
import http.server

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            # Try to write to disk - fails if full
            try:
                with open("/tmp/healthcheck", "w") as f:
                    f.write("ok")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            except OSError:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"disk error")
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    http.server.HTTPServer(("", 8080), Handler).serve_forever()
