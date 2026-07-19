"""The one loopback surface Fun Time exposes to Chrome.

Two things over there need to ask this session something, and neither can read
a file to find it out:

* Tampermonkey fetches the Provider autofill userscript.  It is hand-installed
  once, but Chrome refuses to *update* a script from a ``file://`` path, so
  every later edit used to mean copy-all-paste-save into the Tampermonkey
  dashboard by hand.  The script now carries ``@updateURL`` / ``@downloadURL``
  pointing here, and every merge lands on its next update check.
* The RFB tab pages ask whether the session is in OmniPause, and freeze the
  clip they are showing while it is.  A page is a ``file://`` document with no
  way to watch the state dir, so it polls (see ``omnipause_url``).

The server binds ``127.0.0.1`` only, 404s every other path, and reads both the
script and the pause state per request — an edit or a pause is picked up
without a restart.  It runs as a daemon thread off the orchestrator, so it is
up exactly as long as Fun Time is.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Baked into the userscript's @updateURL/@downloadURL header, so the two must
# stay in lockstep (a regression test pins them). 127.0.0.1-only.
LOOPBACK_PORT = 8770
USERSCRIPT_NAME = "regen_autofill.user.js"
# The real script is provider-specific and git-ignored; a public checkout has
# only this committed template, which the server falls back to serving.
EXAMPLE_USERSCRIPT_NAME = "regen_autofill.example.user.js"

# Polled by the RFB tab pages, which freeze their clips while the session is
# in OmniPause.
OMNIPAUSE_PATH = "/omnipause"


def userscript_path() -> Path:
    """Absolute path to the served userscript inside the package's static dir.

    Prefer the real (git-ignored) script; fall back to the committed example so a
    fresh checkout still serves a valid userscript.
    """
    static = Path(__file__).resolve().parent / "static"
    real = static / USERSCRIPT_NAME
    return real if real.exists() else static / EXAMPLE_USERSCRIPT_NAME


def omnipause_url(port: int = LOOPBACK_PORT) -> str:
    """The URL a page fetches to learn whether the session is in OmniPause.

    Baked into every RFB tab page, so this is the one place the two agree on
    where to ask.
    """
    return f"http://127.0.0.1:{port}{OMNIPAUSE_PATH}"


def _make_handler(script_path: Path, omni_paused: Callable[[], bool]):
    request_path = "/" + USERSCRIPT_NAME

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server API name
            path = self.path.split("?", 1)[0]
            if path == OMNIPAUSE_PATH:
                # Asked per request, never captured at startup: the answer is
                # the whole point and it changes while the page is open.
                body = json.dumps({"omni_paused": omni_paused()}).encode("utf-8")
                # The tab pages read this as origin `null` (they are file://
                # documents), which only a wildcard lets through.
                self._respond(body, "application/json", allow_any_origin=True)
                return
            if path != request_path:
                self.send_error(404)
                return
            try:
                body = script_path.read_bytes()
            except OSError:
                self.send_error(404)
                return
            # A `.user.js` URL served as JavaScript is what Tampermonkey expects.
            self._respond(body, "text/javascript; charset=utf-8")

        def _respond(self, body: bytes, content_type: str, *, allow_any_origin: bool = False) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            if allow_any_origin:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence http.server's stderr logging
            pass

    return _Handler


def make_server(
    port: int = LOOPBACK_PORT,
    script_path: Path | None = None,
    omni_paused: Callable[[], bool] = lambda: False,
) -> ThreadingHTTPServer:
    """Build (but do not start) the loopback server.

    ``omni_paused`` is called per request rather than read once, so it can be
    the live dispatch state.  Unwired, it reports a session that is playing —
    the answer that leaves a clip alone.
    """
    return ThreadingHTTPServer(
        ("127.0.0.1", port),
        _make_handler(script_path or userscript_path(), omni_paused),
    )


def serve_loopback(
    port: int = LOOPBACK_PORT,
    omni_paused: Callable[[], bool] = lambda: False,
) -> ThreadingHTTPServer:
    """Start the loopback server on a daemon thread; return the server."""
    server = make_server(port, omni_paused=omni_paused)
    threading.Thread(target=server.serve_forever, daemon=True, name="loopback-server").start()
    return server
