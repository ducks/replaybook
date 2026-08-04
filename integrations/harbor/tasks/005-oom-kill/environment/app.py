import http.server


def cache_mb():
    try:
        with open("/app/cache.conf", encoding="utf-8") as config:
            return int(config.read().strip())
    except (OSError, ValueError):
        return 10


cache = b"x" * (cache_mb() * 1024 * 1024)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    http.server.HTTPServer(("", 8080), Handler).serve_forever()
