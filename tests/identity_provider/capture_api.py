"""Test-only Cloudflare HTTP boundary. Holds synthetic emails in memory only."""

from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

_MESSAGES: list[dict[str, Any]] = []


class CaptureHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def respond(self, code: int, payload: object) -> None:
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self.respond(200, {"test_capture": True})
        elif self.path == "/messages" and hmac.compare_digest(
            self.headers.get("x-capture-key", ""), os.environ["LAB_CAPTURE_KEY"]
        ):
            self.respond(200, {"messages": _MESSAGES})
        else:
            self.respond(403, {"error": "not_allowed"})

    def do_POST(self) -> None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/external/api/send_mail" or not 0 < size <= 1048576:
                self.respond(400, {"error": "invalid_request"})
                return
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict) or not isinstance(payload.get("token"), str):
                raise ValueError
            if not hmac.compare_digest(
                self.headers.get("x-custom-auth", ""), os.environ["LAB_SITE_PASSWORD"]
            ) or not hmac.compare_digest(payload["token"], os.environ["LAB_ADDRESS_TOKEN"]):
                self.respond(401, {"error": "not_allowed"})
                return
            if payload.get("to_mail") not in json.loads(os.environ["LAB_RECIPIENTS"]):
                raise ValueError
            if len(_MESSAGES) >= 20:
                self.respond(429, {"error": "test_capture_full"})
                return
            _MESSAGES.append({key: value for key, value in payload.items() if key != "token"})
            self.respond(200, {"status": "ok"})
        except (ValueError, TypeError, KeyError):
            self.respond(400, {"error": "invalid_request"})


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8080), CaptureHandler).serve_forever()
