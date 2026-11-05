from __future__ import annotations

from pathlib import Path

from fun_time.mode_plan import EnhancementLevel
from fun_time.modes import has_an_upscale

UPSCALED = ("2_outbox", "upscaled_by_orientation", "portrait", "provider")


def _upscaled(media_root: Path, name: str) -> Path:
    video = media_root.joinpath(*UPSCALED, name)
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")
    return video


def _original(media_root: Path, name: str) -> Path:
    video = media_root / "1_sorted" / "provider" / "portrait" / name
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"")
    return video


class TestTheEnhancementLevels:
    def test_enhanced_only_keeps_the_clips_with_an_original_beside_their_upscale(self, tmp_path):
        paired = _upscaled(tmp_path, "abc_topaz.mp4")
        _original(tmp_path, "abc.mp4")
        alone = _upscaled(tmp_path, "xyz_topaz.mp4")
        clips = [str(paired), str(alone)]

        assert EnhancementLevel.ENHANCED.keep(clips, has_an_upscale(tmp_path)) == [str(paired)]
        assert EnhancementLevel.ORIGINAL.keep(clips, has_an_upscale(tmp_path)) == [str(alone)]
        assert EnhancementLevel.BOTH.keep(clips, has_an_upscale(tmp_path)) == clips

    def test_without_a_media_root_no_clip_can_be_told_to_have_one(self, tmp_path):
        clip = str(_upscaled(tmp_path, "abc_topaz.mp4"))
        _original(tmp_path, "abc.mp4")

        assert EnhancementLevel.ENHANCED.keep([clip], has_an_upscale(None)) == []
        assert EnhancementLevel.ORIGINAL.keep([clip], has_an_upscale(None)) == [clip]

    def test_the_levels_are_spelled_as_the_phrases_say_them(self):
        assert [level.value for level in EnhancementLevel] == ["enhanced", "original", "both"]
