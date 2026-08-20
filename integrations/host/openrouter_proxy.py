#!/usr/bin/env python3
"""Keep the OpenRouter credential outside an untrusted incident VM."""

from __future__ import annotations

import argparse
import http.client
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class OpenRouterProxy(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], api_key: str, upstream: str):
        super().__init__(address, ProxyHandler)
        parsed = urlsplit(upstream)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("upstream must be an http(s) URL with a hostname")
        self.api_key = api_key
        self.upstream = parsed


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    def forward(self) -> None:
        server = self.server
        assert isinstance(server, OpenRouterProxy)
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length) if length else None
        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in HOP_BY_HOP_HEADERS
            and name.lower() != "authorization"
        }
        headers["Authorization"] = f"Bearer {server.api_key}"

        upstream = server.upstream
        if upstream.scheme == "https":
            connection: http.client.HTTPConnection = http.client.HTTPSConnection(
                upstream.hostname,
                upstream.port,
                timeout=600,
                context=ssl.create_default_context(),
            )
        else:
            connection = http.client.HTTPConnection(
                upstream.hostname, upstream.port, timeout=600
            )
        path = f"{upstream.path.rstrip('/')}{self.path}"
        try:
            connection.request(self.command, path, body=body, headers=headers)
            response = connection.getresponse()
            self.send_response(response.status, response.reason)
            for name, value in response.getheaders():
                if name.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while chunk := response.read(64 * 1024):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, http.client.HTTPException) as error:
            self.send_error(502, f"upstream request failed: {type(error).__name__}")
        finally:
            connection.close()

    do_DELETE = forward
    do_GET = forward
    do_PATCH = forward
    do_POST = forward
    do_PUT = forward


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument(
        "--upstream",
        default=os.environ.get(
            "REPLAYBOOK_OPENAI_UPSTREAM",
            os.environ.get("REPLAYBOOK_OPENROUTER_UPSTREAM", "https://openrouter.ai"),
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    api_key = os.environ.get("REPLAYBOOK_OPENAI_API_KEY") or os.environ.get(
        "OPENROUTER_API_KEY", ""
    )
    if not api_key:
        raise SystemExit(
            "REPLAYBOOK_OPENAI_API_KEY or OPENROUTER_API_KEY is required"
        )
    server = OpenRouterProxy((args.listen, args.port), api_key, args.upstream)
    port = server.server_address[1]
    if args.ready_file:
        args.ready_file.write_text(f"{port}\n")
    print(f"ready on {args.listen}:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
