from __future__ import annotations

from fun_time.gallery import Gallery
from fun_time.media_metadata import mark_favorite
from fun_time.shown_favorite import RECHECK_S, ShownFavorite
from tests.made_up_gallery import a_row


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Galleries:
    def __init__(self, *starred: str) -> None:
        self.starred = set(starred)
        self.reads = 0

    def __call__(self) -> Gallery:
        self.reads += 1
        return Gallery([a_row(f"gen {name}", name, starred=1) for name in self.starred])


def _shown(tmp_path, galleries: Galleries | None = None, clock: Clock | None = None) -> ShownFavorite:
    return ShownFavorite(metadata_root=tmp_path / "metadata", gallery=galleries or Galleries(),
                         clock=clock or Clock())


def test_a_video_its_record_calls_a_favorite_is_one(tmp_path):
    video = tmp_path / "videos" / "video one.mp4"
    mark_favorite(video, tmp_path / "metadata", True)

    assert _shown(tmp_path).of(str(video), held=False) is True
    assert _shown(tmp_path).of(str(tmp_path / "videos" / "video two.mp4"), held=False) is False


def test_an_ai_videos_favorite_is_its_star_in_the_gallery(tmp_path):
    starred = tmp_path / "2D" / "AI" / "video one.mp4"
    plain = tmp_path / "2D" / "AI" / "video two.mp4"

    shown = _shown(tmp_path, Galleries("video one.mp4"))

    assert shown.of(str(starred), held=False) is True
    assert shown.of(str(plain), held=False) is False


def test_nothing_on_screen_is_no_favorite_and_reads_nothing(tmp_path):
    galleries = Galleries()

    assert _shown(tmp_path, galleries).of("", held=False) is False
    assert galleries.reads == 0


def test_the_lock_that_favorites_the_video_shows_its_star_at_once(tmp_path):
    video = tmp_path / "2D" / "AI" / "video one.mp4"
    galleries = Galleries()
    shown = _shown(tmp_path, galleries)
    assert shown.of(str(video), held=False) is False

    galleries.starred.add("video one.mp4")

    assert shown.of(str(video), held=True) is True


def test_the_same_video_held_the_same_way_is_read_again_only_after_a_while(tmp_path):
    video = str(tmp_path / "2D" / "AI" / "video one.mp4")
    galleries, clock = Galleries(), Clock()
    shown = _shown(tmp_path, galleries, clock)

    shown.of(video, held=False)
    clock.now = RECHECK_S / 2
    shown.of(video, held=False)
    assert galleries.reads == 1

    clock.now = RECHECK_S
    shown.of(video, held=False)
    assert galleries.reads == 2
