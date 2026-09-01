#!/usr/bin/env python3
"""Authenticated JSON bridge for Web Sweeper desktop and mobile clients."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlsplit

from controller import SweeperController


def request_path(raw_target: str) -> str:
    """Return the route without cache-busting queries or fragments."""
    return urlsplit(raw_target).path


def response(handler: BaseHTTPRequestHandler, status: int, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.end_headers()
    handler.wfile.write(body)


def handler_factory(controller: SweeperController, token: str, local_no_auth: bool = False):
    class Handler(BaseHTTPRequestHandler):
        def _authorized(self) -> bool:
            if local_no_auth:
                return self.client_address[0] in {"127.0.0.1", "::1"}
            supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
            return bool(token) and hmac.compare_digest(supplied, token)

        def do_OPTIONS(self) -> None:  # noqa: N802
            response(self, 204, {})

        def do_GET(self) -> None:  # noqa: N802
            route = request_path(self.path)
            if not self._authorized():
                response(self, 401, {"error": "unauthorized"})
            elif route == "/api/status":
                response(self, 200, controller.status())
            else:
                response(self, 404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                response(self, 401, {"error": "unauthorized"})
                return
            length = min(int(self.headers.get("Content-Length", "0")), 65536)
            route = request_path(self.path)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
                if route == "/api/action":
                    result = controller.action(str(payload.get("action", "")), str(payload.get("lane", "")))
                    response(self, 202, result)
                elif route == "/api/preferences":
                    controller.save_preferences(payload)
                    response(self, 200, {"saved": True})
                elif route == "/api/navigation":
                    result = controller.navigate(
                        str(payload.get("lane", "")), payload.get("queries", [])
                    )
                    response(self, 202, result)
                else:
                    response(self, 404, {"error": "not found"})
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                response(self, 400, {"error": str(error)})

        def log_message(self, pattern: str, *args: object) -> None:
            print(f"{self.client_address[0]} {pattern % args}")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument(
        "--local-no-auth",
        action="store_true",
        help="allow tokenless loopback clients; never valid with a non-loopback host",
    )
    parser.add_argument("--cert", type=Path)
    parser.add_argument("--key", type=Path)
    args = parser.parse_args()
    token = os.environ.get("WEB_SWEEPER_TOKEN", "")
    if not token and args.token_file:
        token = args.token_file.expanduser().read_text(encoding="utf-8").strip()
    if args.local_no_auth and args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("--local-no-auth is restricted to loopback")
    if not token and not args.local_no_auth:
        raise SystemExit("WEB_SWEEPER_TOKEN is required")
    if args.host not in {"127.0.0.1", "::1", "localhost"} and not (args.cert and args.key):
        raise SystemExit("non-loopback mobile access requires --cert and --key (HTTPS)")
    server = ThreadingHTTPServer(
        (args.host, args.port),
        handler_factory(SweeperController(args.config), token, args.local_no_auth),
    )
    if args.cert and args.key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.cert, args.key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    scheme = "https" if args.cert else "http"
    print(f"Web Sweeper controller listening on {scheme}://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
