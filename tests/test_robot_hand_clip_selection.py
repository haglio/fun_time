from __future__ import annotations

from pathlib import Path

from fun_time.robot_hand.clip_runtime import ClipCacheStore
from fun_time.robot_hand.clip_selection import ClipSelectionController
from fun_time.robot_hand.clip_sequence import ClipSequenceController


class FakeLoader:
    def __init__(self, clip_store: ClipCacheStore, *, is_busy: bool = False, adopt_on_load: bool = False):
        self.clip_store = clip_store
        self.is_busy = is_busy
        self.adopt_on_load = adopt_on_load
        self.load_requests: list[Path] = []
        self.prefetch_requests: list[Path] = []

    def request_clip_load(self, path: Path) -> None:
        self.load_requests.append(path)
        if self.adopt_on_load:
            self.clip_store.clip_cache[path] = {"frames": ["f0"]}

    def request_prefetch(self, path: Path) -> None:
        self.prefetch_requests.append(path)


class FakeRenderer:
    def __init__(self):
        self.current_clip_path: Path | None = None
        self.prepare_calls = 0

    def set_current_clip_path(self, path: Path) -> None:
        self.current_clip_path = path

    def prepare_active_clip_for_current_size(self) -> None:
        self.prepare_calls += 1


class FakeNotifier:
    def __init__(self):
        self.clip_notifications: list[Path] = []

    def notify_clip(self, path: Path) -> None:
        self.clip_notifications.append(path)


def _build_controller(*paths: str, loader_busy: bool = False, adopt_on_load: bool = False):
    clip_store = ClipCacheStore(limit=3)
    sequence = ClipSequenceController([Path(path) for path in paths])
    loader = FakeLoader(clip_store, is_busy=loader_busy, adopt_on_load=adopt_on_load)
    renderer = FakeRenderer()
    notifier = FakeNotifier()
    status_messages: list[str] = []
    shows: list[str] = []
    hides: list[str] = []

    controller = ClipSelectionController(
        sequence=sequence,
        clip_store=clip_store,
        loader=loader,
        renderer=renderer,
        notifier=notifier,
        set_status_text=status_messages.append,
        show_status=lambda: shows.append("show"),
        schedule_status_hide=lambda: hides.append("hide"),
    )
    return controller, clip_store, loader, renderer, notifier, status_messages, shows, hides


def test_set_current_clip_uses_cached_entry_without_loading():
    controller, clip_store, loader, renderer, notifier, _status_messages, _shows, hides = _build_controller("a.mp4", "b.mp4")
    path = Path("b.mp4")
    clip_store.clip_cache[path] = {"frames": ["f0"]}

    controller.set_current_clip(path)

    assert renderer.current_clip_path == path
    assert renderer.prepare_calls == 1
    assert notifier.clip_notifications == [path]
    assert loader.load_requests == []
    assert hides == ["hide"]


def test_set_current_clip_requests_load_for_uncached_entry():
    controller, _clip_store, loader, renderer, notifier, _status_messages, _shows, hides = _build_controller("a.mp4", "b.mp4")
    path = Path("b.mp4")

    controller.set_current_clip(path)

    assert renderer.current_clip_path == path
    assert renderer.prepare_calls == 0
    assert notifier.clip_notifications == [path]
    assert loader.load_requests == [path]
    assert hides == []


def test_set_current_clip_prepares_when_load_adopts_immediately():
    controller, _clip_store, loader, renderer, notifier, _status_messages, _shows, hides = _build_controller(
        "a.mp4",
        "b.mp4",
        adopt_on_load=True,
    )
    path = Path("b.mp4")

    controller.set_current_clip(path)

    assert renderer.current_clip_path == path
    assert renderer.prepare_calls == 1
    assert notifier.clip_notifications == [path]
    assert loader.load_requests == [path]
    assert hides == ["hide"]


def test_step_advances_sequence_and_reports_selected_clip():
    controller, _clip_store, loader, renderer, notifier, status_messages, shows, hides = _build_controller("a.mp4", "b.mp4")

    controller.step(1)

    assert controller.current_number == 2
    assert renderer.current_clip_path == Path("b.mp4")
    assert notifier.clip_notifications == [Path("b.mp4")]
    assert loader.load_requests == [Path("b.mp4")]
    assert status_messages == ["Selected clip: b.mp4"]
    assert shows == ["show"]
    assert hides == ["hide"]


def test_request_nearby_prefetch_uses_first_uncached_neighbor():
    controller, clip_store, loader, _renderer, _notifier, _status_messages, _shows, _hides = _build_controller(
        "a.mp4",
        "b.mp4",
        "c.mp4",
    )
    clip_store.clip_cache[Path("b.mp4")] = {"frames": ["f0"]}

    controller.request_nearby_prefetch()

    assert loader.prefetch_requests == [Path("c.mp4")]


def test_request_nearby_prefetch_skips_when_busy():
    controller, _clip_store, loader, _renderer, _notifier, _status_messages, _shows, _hides = _build_controller(
        "a.mp4",
        "b.mp4",
        loader_busy=True,
    )

    controller.request_nearby_prefetch()

    assert loader.prefetch_requests == []


def test_request_nearby_prefetch_is_empty_for_single_clip():
    controller, _clip_store, loader, _renderer, _notifier, _status_messages, _shows, _hides = _build_controller("solo.mp4")

    controller.request_nearby_prefetch()

    assert loader.prefetch_requests == []
