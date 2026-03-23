from __future__ import annotations

from pathlib import Path

from fun_time.robot_hand.clip_runtime import ClipCacheStore, DecodeRequestState


class TestDecodeRequestState:
    def test_begin_resets_previous_result_and_marks_loading(self):
        state = DecodeRequestState(
            request_id=2,
            loading=False,
            loaded_clip_path=Path("old.mp4"),
            loaded_frames=[1],
            load_error="boom",
            request_id_done=2,
        )

        request_id = state.begin()

        assert request_id == 3
        assert state.loading is True
        assert state.loaded_clip_path is None
        assert state.loaded_frames is None
        assert state.load_error is None
        assert state.request_id_done is None

    def test_take_completed_result_returns_current_result_and_clears_done_flag(self):
        state = DecodeRequestState(request_id=4, loading=True)
        state.record_success(Path("clip.mp4"), ["frame"], 4)

        result = state.take_completed_result()

        assert result == (Path("clip.mp4"), ["frame"], None)
        assert state.loading is False
        assert state.request_id_done is None

    def test_take_completed_result_ignores_stale_result_but_keeps_loading(self):
        state = DecodeRequestState(request_id=5, loading=True)
        state.record_error(Path("stale.mp4"), "bad", 4)

        result = state.take_completed_result()

        assert result is None
        assert state.loading is True
        assert state.request_id_done is None
        assert state.loaded_clip_path is None
        assert state.loaded_frames is None
        assert state.load_error is None


class TestClipCacheStore:
    def test_clip_entry_for_marks_path_recently_used(self):
        store = ClipCacheStore(limit=2)
        first = Path("first.mp4")
        second = Path("second.mp4")
        store.clip_cache[first] = {"pil_frames": [], "photo_frames": [], "photo_size": None}
        store.clip_cache[second] = {"pil_frames": [], "photo_frames": [], "photo_size": None}

        entry = store.clip_entry_for(first)

        assert entry is store.clip_cache[first]
        assert list(store.clip_cache) == [second, first]

    def test_cache_decoded_frames_trims_unprotected_entries(self):
        store = ClipCacheStore(limit=2)
        first = Path("first.mp4")
        second = Path("second.mp4")
        third = Path("third.mp4")

        store.cache_decoded_frames(first, ["a"])
        store.cache_decoded_frames(second, ["b"])
        store.cache_decoded_frames(third, ["c"], protected_paths={second})

        assert list(store.decoded_frame_cache) == [second, third]

    def test_adopt_decoded_frames_populates_clip_cache_and_keeps_protected_current_clip(self):
        store = ClipCacheStore(limit=2)
        current = Path("current.mp4")
        next_path = Path("next.mp4")
        store.clip_cache[current] = {"pil_frames": ["old"], "photo_frames": [None], "photo_size": None}
        store.cache_decoded_frames(next_path, ["f1", "f2"])

        adopted = store.adopt_decoded_frames(next_path, protected_paths={current})

        assert adopted is True
        assert store.clip_cache[current]["pil_frames"] == ["old"]
        assert store.clip_cache[next_path]["pil_frames"] == ["f1", "f2"]
        assert store.clip_cache[next_path]["photo_frames"] == [None, None]
        assert store.clip_cache[next_path]["photo_size"] is None

    def test_adopt_decoded_frames_returns_false_when_missing(self):
        store = ClipCacheStore(limit=1)

        assert store.adopt_decoded_frames(Path("missing.mp4")) is False
