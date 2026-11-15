from __future__ import annotations

import contextlib
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import xr
from app_support.win32 import set_app_user_model_id

from fun_time import preview_marker
from fun_time.config import load_config
from fun_time.manifest import WINDOWS_BRIDGE_MANIFEST_FILENAME, write_manifest_data
from fun_time_vr import app, vr_runtime
from fun_time_vr.app import WhatTheHeadsetReaches
from fun_time_vr.bringup import Bringup
from fun_time_vr.funestras import LoadingScreenUnit, hang_the_room
from fun_time_vr.orchestrator import build_vr_manifest
from fun_time_vr.render import SceneRenderer
from fun_time_vr.scheduling import ahead_of_background_work
from fun_time_vr.vr_runtime import Probe, Readiness, explain
from fun_time_vr.vr_session import VRSession
from tests.logging_state import logging_given_back
from tests.made_up_genau import with_made_up_genau
from tests.openxr_fakes import FakeOpenXR, FakeSceneWindow, state_changed


@pytest.fixture(autouse=True)
def _logging_and_hooks_given_back(monkeypatch):
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    with logging_given_back():
        yield


def _a_manifest(tmp_path: Path) -> Path:
    config = load_config(Path("fun_time_config.example.json"))
    config = with_made_up_genau(
        replace(config, paths=replace(config.paths, state_dir=tmp_path,
                                      broker_state_dir=tmp_path)),
        tmp_path)
    return write_manifest_data(build_vr_manifest(config), tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME)


class _ALoadingScreenAlwaysShowing(LoadingScreenUnit):
    @property
    def wanted(self) -> bool:
        return True

    def render_latest_frame(self) -> None:
        self.showing = True


class _TheRenderer:
    def __init__(self, journal: list[str]) -> None:
        self._journal = journal

    def begin_eye(self, *_clear) -> None:
        pass

    def close(self) -> None:
        self._journal.append("close the renderer")


class _TheRoom:
    def __init__(self, journal: list[str], loading_screen, *, interrupted: bool = False) -> None:
        self._journal = journal
        self._interrupted = interrupted
        self.loading_screen = loading_screen
        self.stop = threading.Event()
        self.workers = ()
        self.pointing = SimpleNamespace(close=lambda: journal.append("put the pointer away"))

    def start_pumping(self, _perf) -> None:
        self._journal.append("start pumping")

    def note_the_wearer(self, worn: bool, *, now: float) -> None:
        self._journal.append(f"the wearer is {'on' if worn else 'off'}")
        if self._interrupted:
            raise KeyboardInterrupt

    def close(self, *, keep_the_loading_screen: bool) -> None:
        self._journal.append(f"close the room (keep the loading screen: {keep_the_loading_screen})")


class _AHeadset:
    def __init__(self, *, readiness=Readiness.READY, opens=True, interrupted=False) -> None:
        self.journal: list[str] = []
        self.runtime = FakeOpenXR(self.journal)
        self.runtime.events.extend(
            [state_changed(xr.SessionState.READY), state_changed(xr.SessionState.FOCUSED)])
        self.scene = FakeSceneWindow(self.journal, asked_to_close=True)
        self.given_to_the_room = {}
        self._readiness = readiness
        self._opens = opens
        self._interrupted = interrupted

    def reaches(self) -> WhatTheHeadsetReaches:
        journal = self.journal
        return WhatTheHeadsetReaches(
            claim_identity=lambda identity: journal.append(f"claim {identity}"),
            ensure_ready=self._ensure_ready,
            say_why=lambda message: journal.append(f"say why: {message}"),
            ahead_of_background_work=self._ahead_of_background_work,
            open_session=self._open_session,
            renderer=lambda: _TheRenderer(journal),
            loading_screen=_ALoadingScreenAlwaysShowing,
            hang_the_room=self._hang_the_room,
            window_events=self._window_events,
        )

    def _ensure_ready(self) -> Probe:
        self.journal.append("make sure the runtime is up")
        return Probe(self._readiness)

    @contextlib.contextmanager
    def _ahead_of_background_work(self):
        self.journal.append("ahead of background work")
        try:
            yield
        finally:
            self.journal.append("back behind background work")

    def _open_session(self) -> Bringup:
        self.journal.append("open the session")
        if not self._opens:
            return Bringup(message="the headset answered and would not open")
        return Bringup(session=VRSession(runtime=self.runtime, scene=self.scene))

    def _hang_the_room(self, launch, **kwargs):
        self.journal.append("hang the room")
        self.given_to_the_room = {"contexts": launch.contexts, **kwargs}
        return _TheRoom(self.journal, kwargs["loading_screen"], interrupted=self._interrupted)

    @staticmethod
    def _window_events():
        return SimpleNamespace(poll_events=lambda: None)


def _enter_vr(tmp_path, headset: _AHeadset) -> int:
    return app.main(["--manifest", str(_a_manifest(tmp_path))], reaches=headset.reaches())


def _the_identity() -> str:
    return preview_marker.session_identity(preview_marker.shown_as())


def test_the_headset_process_reaches_the_real_things_unless_handed_stand_ins():
    reaches = WhatTheHeadsetReaches()

    assert (reaches.claim_identity, reaches.ensure_ready, reaches.ahead_of_background_work,
            reaches.renderer, reaches.loading_screen, reaches.hang_the_room) == (
        set_app_user_model_id, vr_runtime.ensure_ready, ahead_of_background_work,
        SceneRenderer, LoadingScreenUnit, hang_the_room)


class TestEnteringVR:
    def test_fun_times_own_identity_is_claimed_before_anything_else(self, tmp_path):
        headset = _AHeadset()

        _enter_vr(tmp_path, headset)

        assert headset.journal[0] == f"claim {_the_identity()}"

    def test_a_runtime_with_no_headset_says_why_and_opens_nothing(self, tmp_path):
        headset = _AHeadset(readiness=Readiness.NO_HEADSET)

        assert _enter_vr(tmp_path, headset) == 1

        assert headset.journal == [
            f"claim {_the_identity()}", "make sure the runtime is up",
            f"say why: {explain(Probe(Readiness.NO_HEADSET))}"]

    def test_a_session_the_headset_will_not_open_says_why(self, tmp_path):
        headset = _AHeadset(opens=False)

        assert _enter_vr(tmp_path, headset) == 1

        assert headset.journal[-3:] == [
            "open the session", "say why: the headset answered and would not open",
            "back behind background work"]

    def test_the_whole_session_runs_ahead_of_background_work(self, tmp_path):
        headset = _AHeadset()

        _enter_vr(tmp_path, headset)

        journal = headset.journal
        assert journal.index("ahead of background work") < journal.index("open the session")
        assert journal[-1] == "back behind background work"

    def test_the_loading_screen_is_up_before_the_funestras_are_built(self, tmp_path):
        headset = _AHeadset()

        _enter_vr(tmp_path, headset)

        journal = headset.journal
        assert "begin the session" in journal
        assert journal.index("end a frame") < journal.index("hang the room")

    def test_the_room_is_built_on_the_scene_windows_contexts(self, tmp_path):
        headset = _AHeadset()

        _enter_vr(tmp_path, headset)

        assert headset.given_to_the_room["contexts"] is headset.scene.contexts
        assert headset.given_to_the_room["manifest_path"] == tmp_path / WINDOWS_BRIDGE_MANIFEST_FILENAME
        assert isinstance(headset.given_to_the_room["loading_screen"], _ALoadingScreenAlwaysShowing)


class TestLeavingVR:
    def test_closing_the_scene_window_takes_the_room_down_and_lets_the_headset_go(self, tmp_path):
        headset = _AHeadset()

        assert _enter_vr(tmp_path, headset) == 0

        journal = headset.journal
        leaving = journal[journal.index("start pumping"):]
        assert [line for line in leaving if not line.startswith(("end a frame", "release", "done"))] == [
            "start pumping", "the wearer is on",
            "close the room (keep the loading screen: False)", "put the pointer away",
            "close the renderer", "ask the session to exit",
            "destroy Swapchain", "destroy Swapchain", "destroy Space", "destroy Space",
            "destroy Space", "destroy Session", "destroy Instance", "close the scene window",
            "back behind background work"]
        assert headset.runtime.kinds_living() == []

    def test_the_closing_screen_covers_the_teardown(self, tmp_path):
        headset = _AHeadset()

        _enter_vr(tmp_path, headset)

        journal = headset.journal
        assert "end a frame" in journal[journal.index("the wearer is on"):
                                        journal.index("close the room (keep the loading screen: False)")]

    def test_a_session_interrupted_from_the_console_is_taken_down_the_same_way(self, tmp_path):
        headset = _AHeadset(interrupted=True)
        headset.scene.asked_to_close = False

        assert _enter_vr(tmp_path, headset) == 0

        assert "close the renderer" in headset.journal
        assert headset.runtime.kinds_living() == []
