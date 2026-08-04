from http.server import BaseHTTPRequestHandler, HTTPServer

import pg8000.native


def db_check():
    connection = pg8000.native.Connection(
        user="postgres",
        password="password",
        host="db",
        database="appdb",
        timeout=3,
        application_name="checkout",
    )
    try:
        connection.run("SELECT 1")
    finally:
        connection.close()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            return
        try:
            db_check()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        except Exception as error:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(f"db error: {error}".encode())
            print(f"db error: {error}", flush=True)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    print("app listening on :8080", flush=True)
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
