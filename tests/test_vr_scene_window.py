from __future__ import annotations

from types import SimpleNamespace

from fun_time_vr import gl_contexts
from fun_time_vr.gl_contexts import SceneWindow

_A_WINDOW = object()
_ITS_HWND = 4242


def _a_window_system(happened: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        CONTEXT_VERSION_MAJOR=1, CONTEXT_VERSION_MINOR=2, OPENGL_PROFILE=3,
        OPENGL_CORE_PROFILE=4, VISIBLE=5, FALSE=0,
        init=lambda: happened.append("init") or True,
        window_hint=lambda *_a: None,
        create_window=lambda *_a: happened.append("create the window") or _A_WINDOW,
        make_context_current=lambda _window: happened.append("draw here"),
        get_win32_window=lambda _window: _ITS_HWND,
        terminate=lambda: happened.append("terminate"),
    )


def _opened(monkeypatch) -> list[str]:
    happened: list[str] = []
    monkeypatch.setattr(gl_contexts, "glfw", _a_window_system(happened))
    monkeypatch.setattr(gl_contexts, "set_the_icon_alt_tab_draws",
                        lambda hwnd, _icon: happened.append(f"wear the button {hwnd}"))
    monkeypatch.setattr(gl_contexts.preview_marker, "dress_the_window",
                        lambda hwnd: happened.append(f"dress {hwnd}"))
    monkeypatch.setattr(gl_contexts, "draw_nothing_at_all",
                        lambda hwnd: happened.append(f"draw nothing {hwnd}"))
    monkeypatch.setattr(gl_contexts, "minimize_window",
                        lambda hwnd, **_kw: happened.append(f"minimize {hwnd}"))
    SceneWindow().open("Fun Time VR")
    return happened


def test_the_scene_window_wears_its_button_before_it_is_minimized(monkeypatch):
    assert _opened(monkeypatch) == [
        "init", "create the window", "draw here", f"wear the button {_ITS_HWND}",
        f"dress {_ITS_HWND}", f"draw nothing {_ITS_HWND}", f"minimize {_ITS_HWND}"]
