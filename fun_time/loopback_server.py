# Chrome refuses to update a userscript from a file:// path, and a file:// page cannot watch the
# state dir, so both ask this server.
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app_support import ports
from shared_ui.pages import with_family_look

LOOPBACK_PORT = ports.USERSCRIPT_HTTP
USERSCRIPT_NAME = "regen_autofill.user.js"
EXAMPLE_USERSCRIPT_NAME = "regen_autofill.example.user.js"

OMNIPAUSE_PATH = "/omnipause"


def userscript_path() -> Path:
    static = Path(__file__).resolve().parent / "static"
    real = static / USERSCRIPT_NAME
    return real if real.exists() else static / EXAMPLE_USERSCRIPT_NAME


def omnipause_url(port: int = LOOPBACK_PORT) -> str:
    return f"http://127.0.0.1:{port}{OMNIPAUSE_PATH}"


def _make_handler(script_path: Path, omni_paused: Callable[[], bool]):
    request_path = "/" + USERSCRIPT_NAME

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == OMNIPAUSE_PATH:
                body = json.dumps({"omni_paused": omni_paused()}).encode("utf-8")
                self._respond(body, "application/json", allow_any_origin=True)
                return
            if path != request_path:
                self.send_error(404)
                return
            try:
                body = with_family_look(script_path.read_text(encoding="utf-8")).encode("utf-8")
            except OSError:
                self.send_error(404)
                return
            self._respond(body, "text/javascript; charset=utf-8")

        def _respond(self, body: bytes, content_type: str, *, allow_any_origin: bool = False) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            if allow_any_origin:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return _Handler


def make_server(
    port: int = LOOPBACK_PORT,
    script_path: Path | None = None,
    omni_paused: Callable[[], bool] = lambda: False,
) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(
        ("127.0.0.1", port),
        _make_handler(script_path or userscript_path(), omni_paused),
    )


def serve_loopback(
    port: int = LOOPBACK_PORT,
    omni_paused: Callable[[], bool] = lambda: False,
) -> ThreadingHTTPServer:
    server = make_server(port, omni_paused=omni_paused)
    threading.Thread(target=server.serve_forever, daemon=True, name="loopback-server").start()
    return server
