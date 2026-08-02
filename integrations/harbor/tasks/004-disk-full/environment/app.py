import http.server


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            try:
                with open("/tmp/healthcheck", "w") as handle:
                    handle.write("ok")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            except OSError:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"disk error")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.HTTPServer(("", 8080), Handler).serve_forever()
