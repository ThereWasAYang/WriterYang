from __future__ import annotations

import errno
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from novel.core.web_launcher import WEB_HOST_ENV, WEB_PORT_ENV, WEB_URL_ENV
from novel.web_api import handle_api_request


class WebServerError(RuntimeError):
    """Raised when the local Web UI server cannot start."""


_STATIC_DIR = Path(__file__).with_name("web_static")
_STATIC_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}


def index_html() -> str:
    path = _STATIC_DIR / "index.html"
    return path.read_text(encoding="utf-8")


def static_asset_bytes(path: str) -> tuple[bytes, str] | None:
    relative = path.removeprefix("/static/").lstrip("/")
    if not relative or relative.startswith("."):
        return None
    target = (_STATIC_DIR / relative).resolve()
    static_root = _STATIC_DIR.resolve()
    if target != static_root and static_root not in target.parents:
        return None
    if not target.is_file():
        return None
    content_type = _STATIC_CONTENT_TYPES.get(target.suffix)
    if content_type is None:
        return None
    return target.read_bytes(), content_type


def run_web_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    try:
        server = ThreadingHTTPServer((host, port), _handler_class())
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise WebServerError(
                f"端口 {port} 已被占用，无法启动 Web UI。请换一个端口，例如："
                f"novel web --port {port + 1}"
            ) from exc
        raise WebServerError(f"无法在 {host}:{port} 启动 Web UI：{exc}") from exc
    os.environ[WEB_HOST_ENV] = host
    os.environ[WEB_PORT_ENV] = str(port)
    os.environ[WEB_URL_ENV] = f"http://{host}:{port}"
    print(f"WriterYang Web UI: http://{host}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _handler_class() -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self._send_text(200, index_html(), "text/html; charset=utf-8")
                return
            if parsed.path.startswith("/static/"):
                asset = static_asset_bytes(parsed.path)
                if asset is None:
                    self._send_json(404, {"ok": False, "error": "not found"})
                    return
                body, content_type = asset
                self._send_bytes(200, body, content_type)
                return
            if parsed.path.startswith("/api/"):
                status, payload = handle_api_request("GET", parsed.path, parsed.query, None)
                self._send_json(status, payload)
                return
            self._send_json(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if not parsed.path.startswith("/api/"):
                self._send_json(404, {"ok": False, "error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length else b"{}"
            status, payload = handle_api_request("POST", parsed.path, parsed.query, body)
            self._send_json(status, payload)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._send_no_cache_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, status: int, body: str, content_type: str) -> None:
            encoded = body.encode("utf-8")
            self._send_bytes(status, encoded, content_type)

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self._send_no_cache_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_no_cache_headers(self) -> None:
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")

    return Handler
