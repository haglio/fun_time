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
    ):
        self.sequence = sequence
        self.clip_store = clip_store
        self.loader = loader
        self.renderer = renderer
        self.notifier = notifier
        self._pending_path: Path | None = None

    @property
    def count(self) -> int:
        return self.sequence.count

    @property
    def current_number(self) -> int:
        return self.sequence.current_number

    @property
    def current_path(self) -> Path:
        return self.sequence.current_path

    @property
    def pending_clip_name(self) -> str | None:
        return self._pending_path.name if self._pending_path is not None else None

    def set_current_clip(self, path: Path) -> None:
        """Switch to a clip immediately (used for initial load)."""
        self._pending_path = None
        self.renderer.set_current_clip_path(path)
        self.notifier.notify_clip(path)

        if path in self.clip_store.clip_cache:
            self._prepare_active_clip()
            return

        self.loader.request_clip_load(path)
        if path in self.clip_store.clip_cache:
            self._prepare_active_clip()

    def step(self, delta: int) -> None:
        """Advance to next/prev clip.  If the clip is cached, switch
        immediately.  Otherwise keep the current clip playing and defer
        the switch until the new clip is loaded."""
        path = self.sequence.step(delta)

        if path in self.clip_store.clip_cache:
            self._pending_path = None
            self.renderer.set_current_clip_path(path)
            self.notifier.notify_clip(path)
            self._prepare_active_clip()
            return

        # Defer — keep rendering the old clip while the new one loads
        self._pending_path = path
        self.loader.request_clip_load(path)

    def adopt_pending_clip(self) -> bool:
        """Called from the refresh loop.  If a deferred clip has finished
        loading, switch the renderer to it and return True."""
        if self._pending_path is None:
            return False
        if self._pending_path not in self.clip_store.clip_cache:
            return False

        path = self._pending_path
        self._pending_path = None
        self.renderer.set_current_clip_path(path)
        self.notifier.notify_clip(path)
        self._prepare_active_clip()
        return True

    def request_nearby_prefetch(self) -> None:
        if self.sequence.count <= 1 or self.loader.is_busy:
            return

        for candidate in self.sequence.nearby_candidates():
            if candidate not in self.clip_store.clip_cache and candidate not in self.clip_store.decoded_frame_cache:
                self.loader.request_prefetch(candidate)
                return

    def _prepare_active_clip(self) -> None:
        self.renderer.prepare_active_clip_for_current_size()
