"""The other versions of the video on screen, as Kino reads them off the library.

The Funestra puts a version up in the clip's own place; what is pinned here is
which files Kino hands it, read off the library's index, and what it says the
console's version button can do.
"""
from __future__ import annotations

from pathlib import Path

from player_core.playback import Playback
from player_core.playlist import PlaylistItem

from main_player.versions import Versions
from tests.satellite_fakes import FakeSatellitePlayer

_SCENE_ONE = ("Jane Doe - scene one.mp4", "Jane Doe - scene one_topaz.mp4")


def _videos(tmp_path, *names: str) -> list[Path]:
    paths = [tmp_path / name for name in names]
    for path in paths:
        path.write_text("x")
    return paths


def _families(*families: list[Path]) -> dict[Path, list[PlaylistItem]]:
    return {
        video: [PlaylistItem(version) for version in family]
        for family in families for video in family
    }


def _playing(playlist: list[Path], version_index) -> tuple[Versions, Playback, FakeSatellitePlayer]:
    player = FakeSatellitePlayer()
    playback = Playback(playlist, player=player, locked=True)
    return Versions(playback, version_index), playback, player


class TestWhetherThereIsAnythingToCycleTo:
    def test_the_console_draws_its_version_button_lit_only_where_a_press_would_do_something(self, tmp_path):
        big, small, solo = _videos(tmp_path, "Jane-1080p.mp4", "Jane-540.mp4", "solo.mp4")

        paired, _playback, _player = _playing([big], _families([big, small]))
        alone, _playback, _player = _playing([solo], _families([solo]))
        stranger, _playback, _player = _playing([solo], {solo: [PlaylistItem(big), PlaylistItem(small)]})

        assert paired.has_other_versions is True
        assert alone.has_other_versions is False
        assert stranger.has_other_versions is False

    def test_with_no_index_there_is_nothing_to_cycle_to(self, tmp_path):
        (solo,) = _videos(tmp_path, "solo.mp4")
        versions, _playback, _player = _playing([solo], None)

        assert versions.has_other_versions is False


class TestCycling:
    def test_a_singleton_group_is_left_alone(self, tmp_path):
        (solo,) = _videos(tmp_path, "solo.mp4")
        versions, playback, player = _playing([solo], _families([solo]))
        before = list(player.opened)

        versions.cycle(1)

        assert (player.opened, playback.switching_versions) == (before, False)

    def test_it_walks_to_the_next_version_in_the_indexs_order(self, tmp_path):
        big, small = _videos(tmp_path, "Jane-1080p.mp4", "Jane-540.mp4")
        versions, playback, _player = _playing([big], _families([big, small]))

        versions.cycle(1)
        assert playback.showing == small

        versions.cycle(1)
        assert playback.showing == big

    def test_a_family_of_three_walks_forward_and_wraps(self, tmp_path):
        original, upscale, small = _videos(tmp_path, *_SCENE_ONE, "Jane Doe - scene one-540.mp4")
        versions, playback, _player = _playing([original], _families([original, upscale, small]))

        walked = []
        for _ in range(4):
            versions.cycle(1)
            walked.append(playback.showing)

        assert walked == [upscale, small, original, upscale]

    def test_a_step_back_walks_the_family_the_other_way(self, tmp_path):
        original, upscale, small = _videos(tmp_path, *_SCENE_ONE, "Jane Doe - scene one-540.mp4")
        versions, playback, _player = _playing([original], _families([original, upscale, small]))

        walked = []
        for _ in range(4):
            versions.cycle(-1)
            walked.append(playback.showing)

        assert walked == [small, upscale, original, small]

    def test_each_step_opens_the_new_file_from_the_beginning(self, tmp_path):
        original, upscale = _videos(tmp_path, *_SCENE_ONE)
        versions, _playback, player = _playing([original], _families([original, upscale]))
        opened_before = len(player.opened)

        versions.cycle(1)

        assert player.opened[opened_before:] == [upscale]

    def test_a_version_the_index_does_not_know_is_left_alone(self, tmp_path):
        """A playlist can carry a video the index was built without -- Fun Time
        writes one from its own selection -- and cycling it must not swap in
        somebody else's family."""
        stranger, original, upscale = _videos(tmp_path, "Ann Bly - scene two.mp4", *_SCENE_ONE)
        versions, playback, _player = _playing(
            [stranger], {stranger: [PlaylistItem(original), PlaylistItem(upscale)]})

        versions.cycle(1)

        assert playback.showing == stranger

    def test_the_default_is_the_version_the_playlist_gave_not_the_largest(self, tmp_path):
        original, upscale, other = _videos(tmp_path, *_SCENE_ONE, "Ann Bly - scene two.mp4")
        versions, playback, _player = _playing([upscale, other], _families([original, upscale]))
        on_default = [versions.on_default]

        versions.cycle(1)
        playback.step(1)
        playback.step(-1)
        on_default.append(versions.on_default)

        assert (playback.showing, on_default) == (original, [True, False])

    def test_cycling_replaces_in_place_so_navigation_skips_the_alternate(self, tmp_path):
        x, a1, a2, b = _videos(tmp_path, "x.mp4", "Jane-1080p.mp4", "Jane-540.mp4", "b.mp4")
        versions, playback, _player = _playing([x, a1, b], _families([a1, a2]))
        playback.step(1)
        assert playback.showing == a1

        versions.cycle(1)
        assert playback.showing == a2
        assert playback.playlist == [x, a1, b]

        playback.step(-1)
        assert playback.showing == x
