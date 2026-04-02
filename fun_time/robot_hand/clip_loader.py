from __future__ import annotations

import time
from pathlib import Path


class ClipLoadController:
    def __init__(
        self,
        *,
        clip_store,
        load_state,
        prefetch_state,
        current_clip_path_getter,
        decode_clip,
        start_thread,
        logger,
        on_loading_requested,
        on_active_clip_loaded,
        on_error,
    ):
        self.clip_store = clip_store
        self.load_state = load_state
        self.prefetch_state = prefetch_state
        self.current_clip_path_getter = current_clip_path_getter
        self.decode_clip = decode_clip
        self.start_thread = start_thread
        self.logger = logger
        self.on_loading_requested = on_loading_requested
        self.on_active_clip_loaded = on_active_clip_loaded
        self.on_error = on_error

    @property
    def is_busy(self) -> bool:
        return self.load_state.loading or self.prefetch_state.loading

    def request_clip_load(self, path: Path) -> None:
        if path in self.clip_store.clip_cache:
            return

        if self._adopt_decoded_frames(path):
            self.logger.info("Adopted prefetched clip %s", path.name)
            return

        self.logger.info("Loading clip %s (no prefetch available)", path.name)
        request_id = self.load_state.begin()
        self.on_loading_requested(path)
        self.start_thread(
            target=self._loader_thread_fn,
            args=(path, request_id),
            name="robot-hand-loader",
        )

    def request_prefetch(self, path: Path) -> None:
        if path in self.clip_store.clip_cache or path in self.clip_store.decoded_frame_cache:
            return
        if self.is_busy:
            return

        self.logger.info("Prefetching clip %s", path.name)
        request_id = self.prefetch_state.begin()
        self.start_thread(
            target=self._prefetch_thread_fn,
            args=(path, request_id),
            name="robot-hand-prefetch",
        )

    def adopt_loaded_clip_if_ready(self) -> None:
        result = self.load_state.take_completed_result()
        if result is None:
            return

        path, frames, err = result
        if err:
            self.on_error(err)
            return

        self._cache_decoded_frames(path, frames)
        self._adopt_decoded_frames(path)

        if self.current_clip_path_getter() == path:
            self.on_active_clip_loaded()

    def adopt_prefetch_if_ready(self) -> None:
        result = self.prefetch_state.take_completed_result()
        if result is None:
            return

        path, frames, err = result
        if err:
            return

        self.logger.info("Prefetch ready: %s (%d frames)", path.name, len(frames) if frames else 0)
        self._cache_decoded_frames(path, frames)

    def _cache_decoded_frames(self, path: Path, frames: list) -> None:
        self.clip_store.cache_decoded_frames(
            path,
            frames,
            protected_paths={self.current_clip_path_getter()},
        )

    def _adopt_decoded_frames(self, path: Path) -> bool:
        return self.clip_store.adopt_decoded_frames(
            path,
            protected_paths={self.current_clip_path_getter()},
        )

    def _decode_thread_fn(self, path: Path, request_id: int, state, log_error) -> None:
        t0 = time.monotonic()
        try:
            frames = self.decode_clip(path)
            elapsed = time.monotonic() - t0
            self.logger.info("Decoded %s: %d frames in %.2fs", path.name, len(frames), elapsed)
            state.record_success(path, frames, request_id)
        except Exception as exc:
            log_error(path, exc)
            state.record_error(path, str(exc), request_id)

    def _loader_thread_fn(self, path: Path, request_id: int) -> None:
        self._decode_thread_fn(
            path, request_id, self.load_state,
            lambda p, e: self.logger.exception("Failed to decode clip %s", p),
        )

    def _prefetch_thread_fn(self, path: Path, request_id: int) -> None:
        self._decode_thread_fn(
            path, request_id, self.prefetch_state,
            lambda p, e: self.logger.warning("Prefetch decode failed for %s: %s", p, e),
        )
