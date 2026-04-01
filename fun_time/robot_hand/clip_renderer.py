from __future__ import annotations

from pathlib import Path


class ClipRenderController:
    def __init__(
        self,
        *,
        clip_store,
        display_frame_fn,
        logger,
    ):
        self.clip_store = clip_store
        self.display_frame_fn = display_frame_fn
        self.logger = logger
        self.current_clip_path: Path | None = None
        self.current_frame_index: int | None = None

    def set_current_clip_path(self, path: Path | None) -> None:
        self.current_clip_path = path
        self.current_frame_index = None

    def current_clip_entry(self):
        path = self.current_clip_path
        if path is None or path not in self.clip_store.clip_cache:
            return None
        return self.clip_store.clip_cache.get(path)

    def prepare_active_clip_for_current_size(self) -> None:
        path = self.current_clip_path
        if path is None or path not in self.clip_store.clip_cache:
            return

        entry = self.clip_store.clip_entry_for(path)
        if entry["frames"]:
            self.display_frame(0)

    def display_frame(self, index: int) -> bool:
        path = self.current_clip_path
        if path is None or path not in self.clip_store.clip_cache:
            return False

        entry = self.clip_store.clip_entry_for(path)
        frames = entry["frames"]
        if not frames or index < 0 or index >= len(frames):
            return False

        if self.current_frame_index != index:
            self.display_frame_fn(frames[index])
            self.current_frame_index = index
        return True
