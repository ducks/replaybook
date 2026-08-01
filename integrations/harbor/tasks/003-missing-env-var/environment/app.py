import os
import http.server

db_url = os.environ["DATABASE_URL"]


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    http.server.HTTPServer(("", 8080), Handler).serve_forever()
