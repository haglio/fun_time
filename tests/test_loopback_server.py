from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from shared_ui import palette
from shared_ui.spacing import MARGIN_STANDARD

from fun_time import loopback_server
from fun_time.loopback_server import (
    EXAMPLE_USERSCRIPT_NAME,
    LOOPBACK_PORT,
    OMNIPAUSE_PATH,
    USERSCRIPT_NAME,
    PauseWatch,
    make_server,
)


def _serve(server):
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def _get(port: int, path: str):
    return urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)


def test_the_server_binds_localhost_only(tmp_path):
    server = make_server(port=0, script_path=tmp_path / USERSCRIPT_NAME)
    try:
        assert server.socket.getsockname()[0] == "127.0.0.1"
    finally:
        server.server_close()


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


def test_serves_the_userscript_in_the_familys_look(tmp_path):
    script = tmp_path / USERSCRIPT_NAME
    script.write_text("note.style.cssText = 'color:__PALETTE_GREEN__;padding:__SPACING_MARGIN_STANDARD__';\n",
                      encoding="utf-8")
    server = make_server(port=0, script_path=script)
    port = _serve(server)
    try:
        with _get(port, f"/{USERSCRIPT_NAME}") as resp:
            body = resp.read().decode("utf-8")
    finally:
        server.shutdown()
    assert body == f"note.style.cssText = 'color:{palette.as_hex(palette.GREEN)};padding:{MARGIN_STANDARD}px';\n"


def test_reflects_edits_without_a_restart(tmp_path):
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


def test_http_servers_stderr_line_per_request_is_silenced(tmp_path, capsys):
    script = tmp_path / USERSCRIPT_NAME
    script.write_text("// x\n", encoding="utf-8")
    server = make_server(port=0, script_path=script)
    port = _serve(server)
    try:
        with _get(port, f"/{USERSCRIPT_NAME}") as resp:
            resp.read()
    finally:
        server.shutdown()
    assert capsys.readouterr().err == ""


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


def test_a_tab_page_frozen_by_a_pause_hears_it_end_at_its_next_ask(tmp_path):
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
        paused = False
        with _get(port, OMNIPAUSE_PATH) as resp:
            assert json.loads(resp.read()) == {"omni_paused": False}
    finally:
        server.shutdown()


def test_a_tab_page_asking_with_what_it_knows_is_answered_the_moment_the_pause_differs():
    paused = [False]
    looks = []

    def look_again():
        looks.append(paused[0])
        paused[0] = True

    watch = PauseWatch(lambda: paused[0], look_again=look_again, looks=1000)

    assert (watch.once_it_differs_from(False), looks) == (True, [False])


def test_a_pause_that_never_changes_is_answered_after_the_longest_wait():
    looks = []
    watch = PauseWatch(lambda: True, look_again=lambda: looks.append(1), looks=3)

    assert (watch.once_it_differs_from(True), len(looks)) == (True, 3)


def test_a_page_that_says_nothing_of_the_pause_is_answered_at_once():
    watch = PauseWatch(lambda: True, look_again=lambda: pytest.fail("waited"), looks=3)

    assert watch.once_it_differs_from(None) is True


def test_a_tab_page_asking_over_http_is_held_until_the_pause_differs(tmp_path):
    script = tmp_path / USERSCRIPT_NAME
    script.write_text("// x\n", encoding="utf-8")
    paused = [False]

    def look_again():
        paused[0] = True

    server = make_server(port=0, script_path=script,
                         watch=PauseWatch(lambda: paused[0], look_again=look_again))
    port = _serve(server)
    try:
        with _get(port, f"{OMNIPAUSE_PATH}?was=0") as resp:
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


def test_the_example_userscript_points_its_updates_at_this_server():
    text = (Path(loopback_server.__file__).parent / "static" / EXAMPLE_USERSCRIPT_NAME).read_text(encoding="utf-8")
    endpoint = f"http://127.0.0.1:{LOOPBACK_PORT}/{USERSCRIPT_NAME}"
    assert "@updateURL" in text
    assert "@downloadURL" in text
    assert endpoint in text


def test_a_server_not_wired_to_a_session_reports_one_that_is_playing(tmp_path):
    script = tmp_path / USERSCRIPT_NAME
    script.write_text("// x\n", encoding="utf-8")
    server = make_server(port=0, script_path=script)
    port = _serve(server)
    try:
        with _get(port, OMNIPAUSE_PATH) as resp:
            assert json.loads(resp.read()) == {"omni_paused": False}
    finally:
        server.shutdown()


@pytest.mark.parametrize("gone", [ConnectionAbortedError, ConnectionResetError, BrokenPipeError])
def test_a_page_gone_before_its_answer_writes_nothing_to_the_log(tmp_path, capsys, gone):
    server = make_server(port=0, script_path=tmp_path / USERSCRIPT_NAME)
    try:
        try:
            raise gone()
        except gone:
            server.handle_error(None, ("127.0.0.1", 1))
    finally:
        server.server_close()

    assert capsys.readouterr().err == ""


def test_any_other_failure_answering_a_page_is_still_written_to_the_log(tmp_path, capsys):
    server = make_server(port=0, script_path=tmp_path / USERSCRIPT_NAME)
    try:
        try:
            raise ValueError("a real fault")
        except ValueError:
            server.handle_error(None, ("127.0.0.1", 1))
    finally:
        server.server_close()

    assert "a real fault" in capsys.readouterr().err
