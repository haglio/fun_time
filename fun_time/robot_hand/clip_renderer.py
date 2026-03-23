from __future__ import annotations

from pathlib import Path

from .cache_utils import render_queue_for_frame_count


class ClipRenderController:
    def __init__(
        self,
        *,
        clip_store,
        image_label,
        make_photo,
        viewport_getter,
        schedule_after,
        render_batch: int,
        logger,
    ):
        self.clip_store = clip_store
        self.image_label = image_label
        self.make_photo = make_photo
        self.viewport_getter = viewport_getter
        self.schedule_after = schedule_after
        self.render_batch = render_batch
        self.logger = logger
        self.current_clip_path: Path | None = None
        self.current_frame_index: int | None = None
        self.render_queue = render_queue_for_frame_count(0)
        self.render_scheduled = False

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
        size = self.viewport_getter()
        entry["photo_size"] = size
        entry["photo_frames"] = [None] * len(entry["pil_frames"])
        self.render_queue.clear()
        self.render_queue.extend(render_queue_for_frame_count(len(entry["pil_frames"])))

        if entry["pil_frames"]:
            first_idx = 0
            entry["photo_frames"][first_idx] = self.make_photo(entry["pil_frames"][first_idx], *size)
            self._display_photo(entry["photo_frames"][first_idx])
            self.current_frame_index = first_idx

        self.schedule_render_step()

    def schedule_render_step(self) -> None:
        if not self.render_scheduled:
            self.render_scheduled = True
            self.schedule_after(1, self.render_step)

    def render_step(self) -> None:
        try:
            self.render_scheduled = False

            path = self.current_clip_path
            if path is None or path not in self.clip_store.clip_cache:
                return

            entry = self.clip_store.clip_entry_for(path)
            size = self.viewport_getter()
            if entry["photo_size"] != size:
                return

            count = 0
            while self.render_queue and count < self.render_batch:
                idx = self.render_queue.popleft()
                if entry["photo_frames"][idx] is None:
                    entry["photo_frames"][idx] = self.make_photo(entry["pil_frames"][idx], *size)
                count += 1

            idx = self.current_frame_index
            if idx is not None and 0 <= idx < len(entry["photo_frames"]) and entry["photo_frames"][idx] is not None:
                self._display_photo(entry["photo_frames"][idx])

            if self.render_queue:
                self.schedule_render_step()
        except Exception:
            self.logger.exception("render_step failed")

    def ensure_current_frame_photo(self, index: int):
        path = self.current_clip_path
        if path is None or path not in self.clip_store.clip_cache:
            return None

        entry = self.clip_store.clip_entry_for(path)
        size = self.viewport_getter()

        if entry["photo_size"] != size:
            self.prepare_active_clip_for_current_size()
            entry = self.clip_store.clip_entry_for(path)

        if entry["photo_frames"][index] is None:
            entry["photo_frames"][index] = self.make_photo(entry["pil_frames"][index], *size)

        return entry["photo_frames"][index]

    def display_frame(self, index: int) -> bool:
        photo = self.ensure_current_frame_photo(index)
        if photo is None:
            return False

        if self.current_frame_index != index or getattr(self.image_label, "image", None) is not photo:
            self._display_photo(photo)
            self.current_frame_index = index
        return True

    def _display_photo(self, photo) -> None:
        self.image_label.configure(image=photo)
        self.image_label.image = photo
