from __future__ import annotations

from pathlib import Path

from app_support.win32 import TaskbarApp
from PIL import Image
from shared_ui.palette import LOADING_ACCENT, PREVIEW_INK
from shared_ui.preview import Preview

from fun_time import preview_marker
from fun_time.project_paths import PROJECT_ICON
from fun_time.win32_taskbar import APP_USER_MODEL_ID

_A_PREVIEW = Preview(feature="the new reference popup")


def test_a_branch_session_wears_the_preview_its_checkout_describes(monkeypatch):
    monkeypatch.setenv(preview_marker.FLAG, "1")
    monkeypatch.setattr(preview_marker, "preview_of", lambda checkout: _A_PREVIEW)

    assert preview_marker.shown_as() is _A_PREVIEW


def test_the_live_session_wears_no_preview_wherever_it_runs_from(monkeypatch):
    monkeypatch.delenv(preview_marker.FLAG, raising=False)
    monkeypatch.setattr(preview_marker, "preview_of", lambda checkout: _A_PREVIEW)

    assert preview_marker.shown_as() is None


def test_a_branch_session_names_the_feature_it_demos():
    assert preview_marker.app_title(_A_PREVIEW) == "Fun Time — preview of the new reference popup"


def test_the_live_session_is_plain_fun_time():
    assert preview_marker.app_title(None) == "Fun Time"


def test_a_branch_session_gets_a_taskbar_button_of_its_own():
    assert preview_marker.session_identity(_A_PREVIEW) == f"{APP_USER_MODEL_ID}.Preview"


def test_the_live_session_joins_its_pinned_button():
    assert preview_marker.session_identity(None) == APP_USER_MODEL_ID


def test_a_branch_session_hands_out_its_letter_in_the_preview_ink(monkeypatch, tmp_path):
    monkeypatch.setattr(preview_marker, "INKED_ICON_FOLDER", tmp_path)

    inked = preview_marker.icon_file(PROJECT_ICON, _A_PREVIEW)

    assert inked.parent == tmp_path
    with Image.open(inked) as icon:
        icon.size = (256, 256)
        assert icon.convert("RGBA").getpixel((128, 128))[:3] == PREVIEW_INK


def test_the_live_session_hands_out_its_own_icon():
    assert preview_marker.icon_file(PROJECT_ICON, None) == PROJECT_ICON


def test_a_branch_session_writes_its_wordmark_in_the_preview_ink():
    assert preview_marker.wordmark_ink(_A_PREVIEW) == PREVIEW_INK


def test_the_live_session_writes_its_wordmark_in_its_own_tone():
    assert preview_marker.wordmark_ink(None) == LOADING_ACCENT


_DESCRIBED = TaskbarApp(name="Fun Time - preview of the new reference popup",
                        icon=Path("C:/example/preview_icon.ico"),
                        relaunch='wscript.exe "C:/example/session.vbs"')


def _dressed(monkeypatch, *, branch_session: bool) -> list:
    if branch_session:
        monkeypatch.setenv(preview_marker.FLAG, "1")
    else:
        monkeypatch.delenv(preview_marker.FLAG, raising=False)
    monkeypatch.setattr(preview_marker, "preview_of", lambda checkout: _A_PREVIEW)
    monkeypatch.setattr(preview_marker, "described_taskbar_app",
                        {f"{APP_USER_MODEL_ID}.Preview": _DESCRIBED}.get)
    worn: list = []
    monkeypatch.setattr(preview_marker, "dress_window",
                        lambda hwnd, app_id, app: worn.append((hwnd, app_id, app)))
    preview_marker.dress_the_window(4242)
    return worn


def test_a_branch_sessions_window_wears_the_button_the_session_described(monkeypatch):
    assert _dressed(monkeypatch, branch_session=True) == [
        (4242, f"{APP_USER_MODEL_ID}.Preview", _DESCRIBED)]


def test_a_live_sessions_window_leaves_its_button_to_the_pinned_shortcut(monkeypatch):
    assert _dressed(monkeypatch, branch_session=False) == [(4242, APP_USER_MODEL_ID, None)]
