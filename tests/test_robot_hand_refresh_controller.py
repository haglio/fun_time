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
    def __init__(self, *, current_number: int = 2, count: int = 5, pending_clip_name: str | None = None):
        self.current_number = current_number
        self.count = count
        self.step_calls: list[int] = []
        self.prefetch_calls = 0
        self.adopt_calls = 0
        self.pending_clip_name = pending_clip_name

    def step(self, delta: int) -> None:
        self.step_calls.append(delta)

    def adopt_pending_clip(self) -> bool:
        self.adopt_calls += 1
        return False

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
    paused_state: bool = False,
    pending_clip_name: str | None = None,
):
    loading_texts: list[str | None] = []
    show_window_calls: list[str] = []
    hide_window_calls: list[str] = []

    loader = FakeLoader(loading=loading)
    notifier = FakeNotifier()
    renderer = FakeRenderer(
        path=Path(path) if path is not None else None,
        entry=entry,
        current_frame_index=current_frame_index,
    )
    selection = FakeSelection(pending_clip_name=pending_clip_name)
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
        paused_file=Path("paused.txt"),
        beats_per_loop=4.0,
        bpm_smoothing=0.5,
        sync_strength=0.5,
        show_window=lambda: show_window_calls.append("show"),
        hide_window=lambda: hide_window_calls.append("hide"),
        set_loading_text=loading_texts.append,
        logger=logger,
        log_name="robot_hand_listener.log",
        now_source=lambda: 5.0,
        consume_command=lambda _path, logger=None: command,
        read_paused_state=lambda _path, logger=None: paused_state,
    )
    return {
        "controller": controller,
        "loader": loader,
        "notifier": notifier,
        "renderer": renderer,
        "selection": selection,
        "engine": engine,
        "logger": logger,
        "loading_texts": loading_texts,
        "show_window_calls": show_window_calls,
        "hide_window_calls": hide_window_calls,
    }


def test_refresh_displays_active_frame():
    state = SharedState(
        auto_active=True,
        visible=True,
        raw_bpm=120.0,
        beats=4,
        stroke_name="pull",
        pattern_duration=1.5,
        last_msg="AUTO 1",
    )
    entry = {"frames": [object() for _ in range(8)]}
    built = _build_controller(state=state, entry=entry)

    built["controller"].refresh()

    assert built["loader"].loaded_adopt_calls == 1
    assert built["loader"].prefetch_adopt_calls == 1
    assert built["renderer"].display_calls == [5]
    assert built["selection"].prefetch_calls == 1


def test_refresh_skips_display_when_no_frames_are_ready():
    built = _build_controller(loading=True, entry=None)

    built["controller"].refresh()

    assert built["renderer"].display_calls == []
    assert built["selection"].prefetch_calls == 1


def test_refresh_skips_prefetch_when_state_has_error():
    built = _build_controller(state=SharedState(error="boom"))

    built["controller"].refresh()

    assert built["selection"].prefetch_calls == 0


def test_refresh_applies_runtime_commands_through_selection_step():
    built = _build_controller(command="NEXT", entry=None)

    built["controller"].refresh()

    assert built["selection"].step_calls == [1]


def test_refresh_reads_paused_state_file_each_tick():
    entry = {"frames": [object() for _ in range(4)]}
    built = _build_controller(entry=entry, paused_state=True)

    built["controller"].refresh()

    assert built["controller"].rh_paused["value"] is True


def test_refresh_reports_exceptions():
    built = _build_controller(entry=None)
    built["renderer"].current_clip_entry = MagicMock(side_effect=RuntimeError("kaboom"))

    built["controller"].refresh()

    built["logger"].exception.assert_called_once_with("refresh failed")


def test_refresh_sets_loading_text_when_pending_clip():
    entry = {"frames": [object() for _ in range(4)]}
    built = _build_controller(entry=entry, pending_clip_name="next.mp4")

    built["controller"].refresh()

    assert built["loading_texts"][-1] == "Loading next.mp4"


def test_refresh_clears_loading_text_when_no_pending_clip():
    entry = {"frames": [object() for _ in range(4)]}
    built = _build_controller(entry=entry, pending_clip_name=None)

    built["controller"].refresh()

    assert built["loading_texts"][-1] is None


def test_refresh_calls_adopt_pending_clip():
    entry = {"frames": [object() for _ in range(4)]}
    built = _build_controller(entry=entry)

    built["controller"].refresh()

    assert built["selection"].adopt_calls == 1
