import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(0.15)
        self.send_response(503)
        self.end_headers()
        self.wfile.write(b"maintenance")

    def log_message(self, *_):
        pass


HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
