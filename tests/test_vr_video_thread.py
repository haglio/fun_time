from __future__ import annotations

import logging
import threading

import pytest

from fun_time_vr.frame_relay import SLOTS
from fun_time_vr.video_thread import A_STILL_JUST_ENOUGH_FOR_A_FISHEYES_EDGE_PX, VideoThread
from tests.paint_fakes import AGpu, AnEngine, ATarget

A_WINDOW = "the video's context"


class _Contexts:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.closed: list[str] = []
        self.current: list[tuple[str | None, int]] = []
        self.targets: list[_ClosableTarget] = []

    def open(self, name: str) -> str:
        self.opened.append(name)
        return A_WINDOW

    def close(self, window: str) -> None:
        self.closed.append(window)

    def make_current(self, window: str | None) -> None:
        self.current.append((window, threading.get_ident()))

    def gpu(self) -> AGpu:
        return AGpu()

    def render_target(self) -> _ClosableTarget:
        target = _ClosableTarget(len(self.targets))
        self.targets.append(target)
        return target


class _ClosableTarget(ATarget):
    closed = False

    def close(self) -> None:
        self.closed = True


class _Engine(AnEngine):
    duration_ms = 90_000.0
    closed = False

    def __init__(self) -> None:
        super().__init__()
        self.painted_on: list[int] = []
        self.painted_once = threading.Event()

    def render(self, fbo, width, height, *, flip_y=False):
        self.painted_on.append(threading.get_ident())
        self.painted_once.set()
        return super().render(fbo, width, height, flip_y=flip_y)

    def close(self) -> None:
        self.closed = True


class _StuckEngine(_Engine):
    def __init__(self) -> None:
        super().__init__()
        self.let_go = threading.Event()

    def render(self, fbo, width, height, *, flip_y=False):
        self.painted_once.set()
        self.let_go.wait()


class _FailsToPaint(_Engine):
    def render(self, fbo, width, height, *, flip_y=False):
        raise RuntimeError("the video could not be drawn")


def _soon(ask):
    wait = threading.Event()
    for _try in range(500):
        answer = ask()
        if answer:
            return answer
        wait.wait(0.01)
    return None


def _a_thread(engine, contexts=None):
    return VideoThread(contexts or _Contexts(), lambda: engine, 1024, name="main-video")


def test_a_video_paints_on_a_thread_of_its_own_holding_its_own_context():
    contexts, engine = _Contexts(), _Engine()

    thread = _a_thread(engine, contexts)
    engine.painted_once.wait(5)
    thread.close()

    assert contexts.opened == ["main-video"]
    (held, on), *_rest = contexts.current
    assert held == A_WINDOW
    assert on != threading.get_ident()
    assert on in engine.painted_on


def test_it_builds_a_target_for_each_slot_and_one_for_stills_at_the_stills_size():
    contexts, engine = _Contexts(), _Engine()

    thread = _a_thread(engine, contexts)
    thread.close()

    assert len(contexts.targets) == SLOTS + 1
    assert (contexts.targets[-1].width, contexts.targets[-1].height) == A_STILL_JUST_ENOUGH_FOR_A_FISHEYES_EDGE_PX


def test_closing_it_lets_go_of_the_engine_the_targets_and_the_context():
    contexts, engine = _Contexts(), _Engine()

    thread = _a_thread(engine, contexts)
    thread.close()

    assert engine.closed is True
    assert all(target.closed for target in contexts.targets)
    assert contexts.current[-1][0] is None
    assert contexts.closed == [A_WINDOW]


def test_its_length_is_the_engines_once_there_is_one():
    thread = _a_thread(_Engine())

    assert thread.duration_ms == 90_000.0
    thread.close()


def test_an_engine_that_cannot_be_built_fails_the_caller_not_the_thread():
    contexts = _Contexts()

    def no_engine():
        raise OSError("the engine would not load")

    with pytest.raises(OSError, match="would not load"):
        VideoThread(contexts, no_engine, 1024, name="main-video")

    assert contexts.closed == [A_WINDOW]
    assert all(target.closed for target in contexts.targets)


def test_a_video_that_stops_painting_says_so_and_still_lets_go_of_everything(caplog):
    contexts, engine = _Contexts(), _FailsToPaint()

    with caplog.at_level(logging.ERROR):
        thread = _a_thread(engine, contexts)
        thread._thread.join(5)

    assert "The main-video video stopped painting" in caplog.text
    assert engine.closed is True
    thread.close()
    assert contexts.closed == [A_WINDOW]


class _Impatient(VideoThread):
    close_timeout_s = 0.01


def test_a_context_still_inside_the_engine_is_never_freed_under_it(caplog):
    contexts, engine = _Contexts(), _StuckEngine()
    thread = _Impatient(contexts, lambda: engine, 1024, name="main-video")
    engine.painted_once.wait(5)

    with caplog.at_level(logging.ERROR):
        thread.close()

    assert contexts.closed == []
    assert "still inside mpv" in caplog.text
    engine.let_go.set()
    thread._thread.join(5)


def test_a_still_asked_for_is_painted_and_handed_over():
    thread = _a_thread(_Engine())

    thread.ask_for_a_still()
    still = _soon(thread.take_a_still)
    thread.close()

    assert still == ("pixels", *A_STILL_JUST_ENOUGH_FOR_A_FISHEYES_EDGE_PX)


def test_showing_the_newest_frame_copies_what_was_painted():
    thread = _a_thread(_Engine())
    target = _ClosableTarget(9)

    shown = _soon(lambda: thread.show_newest(target))
    thread.close()

    assert shown is True
    assert target.painted is True
