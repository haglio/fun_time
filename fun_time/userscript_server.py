"""Serve the Provider autofill userscript over localhost for Tampermonkey auto-update.

The userscript is hand-installed once, but Chrome blocks updating it from a
``file://`` path, so every later edit used to mean copy-all-paste-save into the
Tampermonkey dashboard by hand.  Instead the script now carries ``@updateURL`` /
``@downloadURL`` pointing at ``http://127.0.0.1:<port>/provider_autofill.user.js``,
and this tiny loopback server hands out the current file straight from the
package's ``static`` dir.  Install once, and every merge lands via Tampermonkey's
update check.

The server binds ``127.0.0.1`` only and serves exactly that one file — every
other path is 404 — and reads the file per request, so an edit is picked up
without a restart.  It runs as a daemon thread off the orchestrator, so it is up
whenever Fun Time is.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Baked into the userscript's @updateURL/@downloadURL header, so the two must
# stay in lockstep (a regression test pins them). 127.0.0.1-only.
USERSCRIPT_PORT = 8770
USERSCRIPT_NAME = "provider_autofill.user.js"


def userscript_path() -> Path:
    """Absolute path to the served userscript inside the package's static dir."""
    return Path(__file__).resolve().parent / "static" / USERSCRIPT_NAME


def _make_handler(script_path: Path):
    request_path = "/" + USERSCRIPT_NAME

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API name
            if self.path.split("?", 1)[0] != request_path:
                self.send_error(404)
                return
            try:
                body = script_path.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            # A `.user.js` URL served as JavaScript is what Tampermonkey expects.
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence http.server's stderr logging
            pass

    return _Handler


def make_server(port: int = USERSCRIPT_PORT, script_path: Path | None = None) -> ThreadingHTTPServer:
    """Build (but do not start) the loopback server that serves the userscript."""
    return ThreadingHTTPServer(("127.0.0.1", port), _make_handler(script_path or userscript_path()))


def serve_userscript_updates(port: int = USERSCRIPT_PORT) -> ThreadingHTTPServer:
    """Start the userscript update server on a daemon thread; return the server."""
    server = make_server(port)
    threading.Thread(target=server.serve_forever, daemon=True, name="userscript-server").start()
    return server
