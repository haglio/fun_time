from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fun_time.robot_hand.clip_loader import ClipLoadController
from fun_time.robot_hand.clip_runtime import ClipCacheStore, DecodeRequestState


class JobStarter:
    def __init__(self):
        self.calls: list[tuple[object, tuple[Path, int], str]] = []

    def __call__(self, *, target, args: tuple[Path, int], name: str) -> None:
        self.calls.append((target, args, name))


def _make_loader(*, current_clip_path: Path | None = None):
    clip_store = ClipCacheStore(limit=2)
    load_state = DecodeRequestState()
    prefetch_state = DecodeRequestState()
    starter = JobStarter()
    logger = MagicMock()
    loading_requested: list[Path] = []
    active_loaded: list[str] = []
    errors: list[str] = []

    controller = ClipLoadController(
        clip_store=clip_store,
        load_state=load_state,
        prefetch_state=prefetch_state,
        current_clip_path_getter=lambda: current_clip_path,
        decode_clip=lambda _path: ["f0", "f1"],
        start_thread=starter,
        logger=logger,
        on_loading_requested=loading_requested.append,
        on_active_clip_loaded=lambda: active_loaded.append("ready"),
        on_error=errors.append,
    )
    return controller, clip_store, load_state, prefetch_state, starter, logger, loading_requested, active_loaded, errors


def test_request_clip_load_adopts_decoded_frames_without_starting_job():
    path = Path("demo.mp4")
    controller, clip_store, _load_state, _prefetch_state, starter, _logger, loading_requested, _active_loaded, _errors = _make_loader()
    clip_store.decoded_frame_cache[path] = ["f0", "f1"]

    controller.request_clip_load(path)

    assert path in clip_store.clip_cache
    assert starter.calls == []
    assert loading_requested == []


def test_request_clip_load_starts_background_job_and_reports_loading():
    path = Path("demo.mp4")
    controller, _clip_store, load_state, _prefetch_state, starter, _logger, loading_requested, _active_loaded, _errors = _make_loader()

    controller.request_clip_load(path)

    assert load_state.loading is True
    assert loading_requested == [path]
    assert [(call[1], call[2]) for call in starter.calls] == [((path, 1), "robot-hand-loader")]


def test_adopt_loaded_clip_if_ready_promotes_frames_and_notifies_current_clip():
    path = Path("demo.mp4")
    controller, clip_store, load_state, _prefetch_state, _starter, _logger, _loading_requested, active_loaded, _errors = _make_loader(current_clip_path=path)
    request_id = load_state.begin()
    load_state.record_success(path, ["f0", "f1"], request_id)

    controller.adopt_loaded_clip_if_ready()

    assert path in clip_store.clip_cache
    assert clip_store.clip_cache[path]["pil_frames"] == ["f0", "f1"]
    assert active_loaded == ["ready"]


def test_adopt_loaded_clip_if_ready_forwards_errors():
    path = Path("demo.mp4")
    controller, _clip_store, load_state, _prefetch_state, _starter, _logger, _loading_requested, _active_loaded, errors = _make_loader()
    request_id = load_state.begin()
    load_state.record_error(path, "boom", request_id)

    controller.adopt_loaded_clip_if_ready()

    assert errors == ["boom"]


def test_request_prefetch_skips_when_busy():
    path = Path("demo.mp4")
    controller, _clip_store, load_state, _prefetch_state, starter, _logger, _loading_requested, _active_loaded, _errors = _make_loader()
    load_state.begin()

    controller.request_prefetch(path)

    assert starter.calls == []


def test_request_prefetch_starts_background_job_for_uncached_path():
    path = Path("demo.mp4")
    controller, _clip_store, _load_state, prefetch_state, starter, _logger, _loading_requested, _active_loaded, _errors = _make_loader()

    controller.request_prefetch(path)

    assert prefetch_state.loading is True
    assert [(call[1], call[2]) for call in starter.calls] == [((path, 1), "robot-hand-prefetch")]


def test_adopt_prefetch_if_ready_caches_frames_without_active_notification():
    path = Path("demo.mp4")
    controller, clip_store, _load_state, prefetch_state, _starter, _logger, _loading_requested, active_loaded, _errors = _make_loader()
    request_id = prefetch_state.begin()
    prefetch_state.record_success(path, ["f0"], request_id)

    controller.adopt_prefetch_if_ready()

    assert clip_store.decoded_frame_cache[path] == ["f0"]
    assert active_loaded == []
