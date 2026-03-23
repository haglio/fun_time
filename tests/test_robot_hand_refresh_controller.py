from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fun_time.robot_hand.engine import PlaybackEngine
from fun_time.robot_hand.refresh_controller import RobotHandRefreshController
from fun_time.robot_hand.state import SharedState


class FakeLoader:
    def __init__(self, *, loading: bool = False):
        self.load_state = type("LoadState", (), {"loading": loading})()
        self.loaded_adopt_calls = 0
        self.prefetch_adopt_calls = 0

    def adopt_loaded_clip_if_ready(self) -> None:
        self.loaded_adopt_calls += 1

    def adopt_prefetch_if_ready(self) -> None:
        self.prefetch_adopt_calls += 1


class FakeNotifier:
    def __init__(self, *, window_visible: bool = False):
        self.window_visible = window_visible
        self.calls: list[dict] = []

    def sync_window_visibility(self, **kwargs):
        self.calls.append(kwargs)
        return self.window_visible


class FakeRenderer:
    def __init__(self, *, path: Path | None = None, entry=None, current_frame_index: int | None = None):
        self.current_clip_path = path
        self._entry = entry
        self.current_frame_index = current_frame_index
        self.display_calls: list[int] = []

    def current_clip_entry(self):
        return self._entry

    def display_frame(self, index: int) -> None:
        self.display_calls.append(index)


class FakeSelection:
    def __init__(self, *, current_number: int = 2, count: int = 5):
        self.current_number = current_number
        self.count = count
        self.step_calls: list[int] = []
        self.prefetch_calls = 0

    def step(self, delta: int) -> None:
        self.step_calls.append(delta)

    def request_nearby_prefetch(self) -> None:
        self.prefetch_calls += 1


def _build_controller(
    *,
    state: SharedState | None = None,
    loading: bool = False,
    path: str | None = "demo.mp4",
    entry=None,
    current_frame_index: int | None = None,
    command: str | None = None,
):
    schedule_calls: list[tuple[int, object]] = []
    status_messages: list[str] = []
    overlay_shows: list[str] = []
    show_window_calls: list[str] = []
    hide_window_calls: list[str] = []

    loader = FakeLoader(loading=loading)
    notifier = FakeNotifier()
    renderer = FakeRenderer(
        path=Path(path) if path is not None else None,
        entry=entry,
        current_frame_index=current_frame_index,
    )
    selection = FakeSelection()
    engine = PlaybackEngine(phase=0.25, last_tick=5.0)
    logger = MagicMock()
    controller = RobotHandRefreshController(
        state=state or SharedState(),
        loader=loader,
        notifier=notifier,
        renderer=renderer,
        selection=selection,
        engine=engine,
        rh_paused={"value": False},
        command_file=Path("command.txt"),
        beats_per_loop=4.0,
        bpm_smoothing=0.5,
        sync_strength=0.5,
        schedule_after=lambda delay, callback: schedule_calls.append((delay, callback)),
        show_window=lambda: show_window_calls.append("show"),
        hide_window=lambda: hide_window_calls.append("hide"),
        set_status_text=status_messages.append,
        show_status=lambda: overlay_shows.append("show"),
        logger=logger,
        log_name="robot_hand_listener.log",
        now_source=lambda: 5.0,
        consume_command=lambda _path, logger=None: command,
    )
    return {
        "controller": controller,
        "loader": loader,
        "notifier": notifier,
        "renderer": renderer,
        "selection": selection,
        "engine": engine,
        "logger": logger,
        "schedule_calls": schedule_calls,
        "status_messages": status_messages,
        "overlay_shows": overlay_shows,
        "show_window_calls": show_window_calls,
        "hide_window_calls": hide_window_calls,
    }


def test_refresh_displays_active_frame_and_schedules_next_tick():
    state = SharedState(
        auto_active=True,
        visible=True,
        raw_bpm=120.0,
        beats=4,
        stroke_name="pull",
        pattern_duration=1.5,
        last_msg="AUTO 1",
    )
    entry = {"pil_frames": [object() for _ in range(8)]}
    built = _build_controller(state=state, entry=entry)

    built["controller"].refresh()

    assert built["loader"].loaded_adopt_calls == 1
    assert built["loader"].prefetch_adopt_calls == 1
    assert built["renderer"].display_calls == [5]
    assert built["selection"].prefetch_calls == 1
    assert built["schedule_calls"] == [(16, built["controller"].refresh)]
    assert "clip=demo.mp4" in built["status_messages"][-1]
    assert "frame=6/8" in built["status_messages"][-1]
    assert "visible=True" in built["status_messages"][-1]


def test_refresh_shows_loading_status_when_no_frames_are_ready():
    built = _build_controller(loading=True, entry=None)

    built["controller"].refresh()

    assert built["renderer"].display_calls == []
    assert built["selection"].prefetch_calls == 1
    assert built["overlay_shows"] == ["show"]
    assert built["schedule_calls"] == [(16, built["controller"].refresh)]
    assert built["status_messages"][-1].startswith("clip=demo.mp4")
    assert "loading=True" in built["status_messages"][-1]


def test_refresh_uses_listener_error_status_and_short_retry_when_state_has_error():
    built = _build_controller(state=SharedState(error="boom"))

    built["controller"].refresh()

    assert built["selection"].prefetch_calls == 0
    assert built["overlay_shows"] == ["show"]
    assert built["schedule_calls"] == [(100, built["controller"].refresh)]
    assert built["status_messages"] == ["Error:\nboom"]


def test_refresh_applies_runtime_commands_through_selection_step():
    built = _build_controller(command="NEXT", entry=None)

    built["controller"].refresh()

    assert built["selection"].step_calls == [1]
    assert built["schedule_calls"] == [(16, built["controller"].refresh)]


def test_refresh_reports_exceptions_and_schedules_retry():
    built = _build_controller(entry=None)
    built["renderer"].current_clip_entry = MagicMock(side_effect=RuntimeError("kaboom"))

    built["controller"].refresh()

    built["logger"].exception.assert_called_once_with("refresh failed")
    assert built["overlay_shows"] == ["show"]
    assert built["schedule_calls"] == [(250, built["controller"].refresh)]
    assert built["status_messages"] == ["Error: kaboom\nSee robot_hand_listener.log"]
