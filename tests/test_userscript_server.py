"""The userscript update server hands Tampermonkey the current autofill script."""
from __future__ import annotations

import threading
import urllib.error
import urllib.request

import pytest

from fun_time.userscript_server import (
    USERSCRIPT_NAME,
    USERSCRIPT_PORT,
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


def test_real_userscript_points_its_updates_at_this_server():
    # A desync between the header URL and USERSCRIPT_PORT silently breaks
    # auto-update, so pin them together.
    text = userscript_path().read_text(encoding="utf-8")
    endpoint = f"http://127.0.0.1:{USERSCRIPT_PORT}/{USERSCRIPT_NAME}"
    assert "@updateURL" in text
    assert "@downloadURL" in text
    assert endpoint in text
