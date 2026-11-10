from __future__ import annotations

from pathlib import Path

import pytest
from app_support import kino_folder
from app_support.media_renditions import renditions

from fun_time.mode_plan import EnhancementLevel
from tests.made_up_genau import kino_upscales_at


@pytest.fixture(autouse=True)
def _kino(tmp_path, monkeypatch):
    return kino_upscales_at(monkeypatch, tmp_path)


def _upscaled(media_root: Path, name: str) -> Path:
    video = kino_folder.upscales_in(kino_folder.kino_dir()) / "portrait" / "provider" / name
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")
    return video


def _original(media_root: Path, name: str) -> Path:
    video = media_root / "1_sorted" / "provider" / "portrait" / name
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")
    return video


def _has_an_upscale_in(media_root: Path):
    return lambda clip: bool(renditions(clip, media_root))


class TestTheEnhancementLevels:
    def test_enhanced_only_keeps_the_clips_with_an_original_beside_their_upscale(self, tmp_path):
        paired = _upscaled(tmp_path, "abc_topaz.mp4")
        _original(tmp_path, "abc.mp4")
        alone = _upscaled(tmp_path, "xyz_topaz.mp4")
        clips = [str(paired), str(alone)]

        assert EnhancementLevel.ENHANCED.keep(clips, _has_an_upscale_in(tmp_path)) == [str(paired)]
        assert EnhancementLevel.ORIGINAL.keep(clips, _has_an_upscale_in(tmp_path)) == [str(alone)]
        assert EnhancementLevel.BOTH.keep(clips, _has_an_upscale_in(tmp_path)) == clips


    def test_the_levels_are_spelled_as_the_phrases_say_them(self):
        assert [level.value for level in EnhancementLevel] == ["enhanced", "original", "both"]
