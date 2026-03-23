from __future__ import annotations

from pathlib import Path


class ClipSelectionController:
    def __init__(
        self,
        *,
        sequence,
        clip_store,
        loader,
        renderer,
        notifier,
        set_status_text,
        show_status,
        schedule_status_hide,
    ):
        self.sequence = sequence
        self.clip_store = clip_store
        self.loader = loader
        self.renderer = renderer
        self.notifier = notifier
        self.set_status_text = set_status_text
        self.show_status = show_status
        self.schedule_status_hide = schedule_status_hide

    @property
    def count(self) -> int:
        return self.sequence.count

    @property
    def current_number(self) -> int:
        return self.sequence.current_number

    @property
    def current_path(self) -> Path:
        return self.sequence.current_path

    def set_current_clip(self, path: Path) -> None:
        self.renderer.set_current_clip_path(path)
        self.notifier.notify_clip(path)

        if path in self.clip_store.clip_cache:
            self._prepare_active_clip()
            return

        self.loader.request_clip_load(path)
        if path in self.clip_store.clip_cache:
            self._prepare_active_clip()

    def step(self, delta: int) -> None:
        path = self.sequence.step(delta)
        self.set_current_clip(path)
        self.set_status_text(f"Selected clip: {path.name}")
        self.show_status()
        self.schedule_status_hide()

    def request_nearby_prefetch(self) -> None:
        if self.sequence.count <= 1 or self.loader.is_busy:
            return

        for candidate in self.sequence.nearby_candidates():
            if candidate not in self.clip_store.clip_cache and candidate not in self.clip_store.decoded_frame_cache:
                self.loader.request_prefetch(candidate)
                return

    def _prepare_active_clip(self) -> None:
        self.renderer.prepare_active_clip_for_current_size()
        self.schedule_status_hide()
