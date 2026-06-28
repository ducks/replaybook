import os
import http.server

# Load a cache on startup - size controlled by env var
CACHE_MB = int(os.environ.get("CACHE_MB", "10"))
cache = b"x" * (CACHE_MB * 1024 * 1024)

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    http.server.HTTPServer(("", 8080), Handler).serve_forever()
