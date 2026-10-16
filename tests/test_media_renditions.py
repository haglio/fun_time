from __future__ import annotations

from pathlib import Path

from fun_time.media_renditions import has_an_upscale, original_rendition, renditions
from fun_time.mode_plan import EnhancementLevel

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


def test_finds_the_pre_upscale_original(tmp_path: Path):
    """The library sorts by source/orientation, then upscales by orientation/source."""
    upscaled = _upscaled(tmp_path, "abc_topaz.mp4")
    original = _original(tmp_path, "abc.mp4")

    assert original_rendition(str(upscaled), tmp_path) == str(original)


def test_finds_an_original_that_was_never_upscale_suffixed(tmp_path: Path):
    upscaled = _upscaled(tmp_path, "abc.mp4")
    original = _original(tmp_path, "abc.mp4")

    assert original_rendition(str(upscaled), tmp_path) == str(original)


def test_no_original_on_disk(tmp_path: Path):
    upscaled = _upscaled(tmp_path, "abc_topaz.mp4")

    assert original_rendition(str(upscaled), tmp_path) == ""


def test_video_outside_the_upscale_tree(tmp_path: Path):
    stray = tmp_path / "1_sorted" / "provider" / "portrait" / "abc.mp4"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"")

    assert original_rendition(str(stray), tmp_path) == ""


def test_video_outside_the_media_root(tmp_path: Path):
    assert original_rendition(r"C:\elsewhere\abc_topaz.mp4", tmp_path) == ""


def test_no_media_root_configured(tmp_path: Path):
    upscaled = _upscaled(tmp_path, "abc_topaz.mp4")

    assert original_rendition(str(upscaled), None) == ""


def test_no_video_path(tmp_path: Path):
    assert original_rendition("", tmp_path) == ""


class TestTheEnhancementLevels:
    """What a Funestra plays, by whether an enhanced version exists: the
    satellites' clips have one when the upscale on screen has its original
    beside it, which is what the versions button switches between."""

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


class TestTheVersionsAClipCanBeSwitchedBetween:
    def test_the_clip_leads_its_own_family(self, tmp_path: Path):
        """The upscale is what the library plays and what everything records, so
        it is the version a clip comes up on and the one a family opens with."""
        upscaled = _upscaled(tmp_path, "abc_topaz.mp4")
        original = _original(tmp_path, "abc.mp4")

        assert renditions(str(upscaled), tmp_path) == [str(upscaled), str(original)]

    def test_a_clip_whose_original_is_gone_has_no_family(self, tmp_path: Path):
        """One version is nothing to switch between, and the console's button is
        dim for it."""
        upscaled = _upscaled(tmp_path, "abc_topaz.mp4")

        assert renditions(str(upscaled), tmp_path) == []

    def test_a_clip_outside_the_upscale_tree_has_no_family(self, tmp_path: Path):
        stray = tmp_path / "1_sorted" / "provider" / "portrait" / "abc.mp4"
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_bytes(b"")

        assert renditions(str(stray), tmp_path) == []
