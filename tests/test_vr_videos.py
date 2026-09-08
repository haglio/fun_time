"""Which videos are VR masters, and the browse filter built on the answer."""
from __future__ import annotations

import random
from pathlib import Path

from fun_time.modes import VideoShapes, build_main_playlist_paths
from fun_time.vr_videos import is_vr_video, keep_shapes


class TestIsVrVideo:
    def test_a_video_under_a_vr_library_dir_is_one(self, tmp_path: Path):
        assert is_vr_video(tmp_path / "vr" / "deep" / "scene.mp4", [tmp_path / "vr"])

    def test_a_video_outside_every_vr_dir_is_not(self, tmp_path: Path):
        assert not is_vr_video(tmp_path / "flat" / "scene.mp4", [tmp_path / "vr"])

    def test_a_name_that_says_its_mastering_is_one_wherever_it_lives(self, tmp_path: Path):
        """The mastering travels with the file: a master moved out of the VR
        library still wraps the view."""
        assert is_vr_video(tmp_path / "flat" / "scene_180_sbs.mp4", [tmp_path / "vr"])
        assert is_vr_video(tmp_path / "flat" / "scene_mkx200.mp4", [])

    def test_a_sibling_dir_whose_name_merely_starts_the_same_is_outside(self, tmp_path: Path):
        assert not is_vr_video(tmp_path / "vr_old" / "scene.mp4", [tmp_path / "vr"])


class TestKeepShapes:
    def _paths(self, tmp_path: Path) -> tuple[list[str], Path]:
        vr_dir = tmp_path / "vr"
        return [str(vr_dir / "wrap.mp4"), str(tmp_path / "flat" / "screen.mp4")], vr_dir

    def test_both_shapes_keeps_everything(self, tmp_path: Path):
        paths, vr_dir = self._paths(tmp_path)

        assert keep_shapes(paths, vr_dirs=[vr_dir], plays_vr=True, plays_flat=True) == paths

    def test_vr_only_keeps_the_masters(self, tmp_path: Path):
        paths, vr_dir = self._paths(tmp_path)

        kept = keep_shapes(paths, vr_dirs=[vr_dir], plays_vr=True, plays_flat=False)

        assert kept == [paths[0]]

    def test_flat_only_keeps_the_others(self, tmp_path: Path):
        paths, vr_dir = self._paths(tmp_path)

        kept = keep_shapes(paths, vr_dirs=[vr_dir], plays_vr=False, plays_flat=True)

        assert kept == [paths[1]]

    def test_neither_shape_keeps_nothing(self, tmp_path: Path):
        """Degenerate, and the answer that was asked for: the caller decides what
        an empty browse means rather than this widening one nobody widened."""
        paths, vr_dir = self._paths(tmp_path)

        assert keep_shapes(paths, vr_dirs=[vr_dir], plays_vr=False, plays_flat=False) == []


class TestVideoShapes:
    def test_a_session_with_no_vr_library_offers_no_choice(self):
        assert VideoShapes().offered is False

    def test_a_session_whose_sources_hold_both_offers_one(self, tmp_path: Path):
        assert VideoShapes(vr_dirs=str(tmp_path / "vr")).offered is True

    def test_the_main_build_narrows_to_the_shape_asked_for(self, tmp_path: Path):
        vr_dir = tmp_path / "vr"
        flat_dir = tmp_path / "flat"
        for folder, name in ((vr_dir, "wrap.mp4"), (flat_dir, "screen.mp4")):
            folder.mkdir(parents=True)
            (folder / name).write_text("x", encoding="utf-8")
        sources = f"{vr_dir}|{flat_dir}"

        every = build_main_playlist_paths(sources, False, rng=random.Random(1))
        vr_only = build_main_playlist_paths(
            sources, False, rng=random.Random(1),
            shapes=VideoShapes(vr_dirs=str(vr_dir), plays_flat=False))

        assert len(every) == 2
        assert [Path(p).name for p in vr_only] == ["wrap.mp4"]
