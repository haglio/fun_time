"""The loopback server answers Chrome: the autofill script, and the pause state."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from fun_time.loopback_server import (
    LOOPBACK_PORT,
    OMNIPAUSE_PATH,
    USERSCRIPT_NAME,
    make_server,
    userscript_path,
)


def _serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def _get(port: int, path: str):
    return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)


def test_serves_the_userscript_with_a_javascript_content_type(tmp_path):
    script = tmp_path / USERSCRIPT_NAME
    script.write_bytes(b"// unit-test userscript\n")
    server = make_server(port=0, script_path=script)
    port = _serve(server)
    try:
        with _get(port, f"/{USERSCRIPT_NAME}") as resp:
            body = resp.read()
            content_type = resp.headers.get("Content-Type", "")
    finally:
        server.shutdown()
    assert body == script.read_bytes()
    assert "javascript" in content_type


def test_reflects_edits_without_a_restart(tmp_path):
    # Tampermonkey must fetch the current file, not a snapshot from launch time.
    script = tmp_path / USERSCRIPT_NAME
    script.write_bytes(b"// v1\n")
    server = make_server(port=0, script_path=script)
    port = _serve(server)
    try:
        script.write_bytes(b"// v2\n")
        with _get(port, f"/{USERSCRIPT_NAME}") as resp:
            assert resp.read() == b"// v2\n"
    finally:
        server.shutdown()


def test_other_paths_are_404(tmp_path):
    script = tmp_path / USERSCRIPT_NAME
    script.write_text("// x\n", encoding="utf-8")
    server = make_server(port=0, script_path=script)
    port = _serve(server)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            _get(port, "/anything-else")
        assert exc.value.code == 404
    finally:
        server.shutdown()


def test_reports_the_live_omnipause_state(tmp_path):
    # Read per request, not captured at startup: the RFB tab pages poll this to
    # decide whether to freeze their clips, so a stale answer freezes forever.
    script = tmp_path / USERSCRIPT_NAME
    script.write_text("// x\n", encoding="utf-8")
    paused = False
    server = make_server(port=0, script_path=script, omni_paused=lambda: paused)
    port = _serve(server)
    try:
        with _get(port, OMNIPAUSE_PATH) as resp:
            assert json.loads(resp.read()) == {"omni_paused": False}
        paused = True
        with _get(port, OMNIPAUSE_PATH) as resp:
            assert json.loads(resp.read()) == {"omni_paused": True}
    finally:
        server.shutdown()


def test_omnipause_is_readable_from_a_file_uri_page(tmp_path):
    # The tab pages are file:// documents, so they fetch this as origin `null`.
    # Chrome drops the response without a header opting that origin in.
    script = tmp_path / USERSCRIPT_NAME
    script.write_text("// x\n", encoding="utf-8")
    server = make_server(port=0, script_path=script)
    port = _serve(server)
    try:
        with _get(port, OMNIPAUSE_PATH) as resp:
            assert resp.headers.get("Access-Control-Allow-Origin") == "*"
    finally:
        server.shutdown()


def test_real_userscript_points_its_updates_at_this_server():
    # A desync between the header URL and LOOPBACK_PORT silently breaks
    # auto-update, so pin them together.
    text = userscript_path().read_text(encoding="utf-8")
    endpoint = f"http://127.0.0.1:{LOOPBACK_PORT}/{USERSCRIPT_NAME}"
    assert "@updateURL" in text
    assert "@downloadURL" in text
    assert endpoint in text
