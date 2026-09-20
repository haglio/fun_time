from __future__ import annotations

from pathlib import Path

from fun_time.media_renditions import original_rendition, renditions

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
