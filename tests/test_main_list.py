from __future__ import annotations

import os
from pathlib import Path

from fun_time.main_list import MainList, MainListSettings
from fun_time.modes import build_generated_marker_path


def _videos(folder: Path, *names: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    videos = [folder / name for name in names]
    for video in videos:
        video.write_text("x", encoding="utf-8")
    return videos


def _the_list(tmp_path: Path, sources: str, **where) -> MainList:
    return MainList(sources=sources, state_dir=tmp_path / "state", **where)


class TestFMode:
    def test_it_keeps_the_hand_scripted_video_and_drops_the_bulk_scripted_one(self, tmp_path):
        """The bulk run is what made "has a funscript" stop meaning anything: the
        second video has a script too, and is exactly what F-mode is for excluding."""
        root = tmp_path / "videos" / "videos" / "primary"
        hand, bulk, plain = _videos(root, "a.mp4", "b.mp4", "c.mp4")
        scripts = tmp_path / "videos" / "scripts" / "scripts" / "primary"
        scripts.mkdir(parents=True)
        for video in (hand, bulk):
            (scripts / f"{video.stem}.funscript").write_text("{}", encoding="utf-8")
        marker = Path(build_generated_marker_path(str(bulk)))
        marker.parent.mkdir(parents=True)
        marker.touch()

        videos = _the_list(tmp_path, str(root)).videos(
            MainListSettings(scripted_filter=True))

        assert videos == [str(hand)]

    def test_off_it_holds_every_video(self, tmp_path):
        root = tmp_path / "vids"
        _videos(root, "a.mp4", "b.mp4")

        videos = _the_list(tmp_path, str(root)).videos(MainListSettings())

        assert len(videos) == 2

    def test_it_takes_scripted_videos_from_sub_folders_too(self, tmp_path):
        root = tmp_path / "videos" / "videos" / "2D" / "non_AI"
        top = root / "clip.mp4"
        nested = root / "batch" / "portrait" / "nested_clip.mp4"
        scripts = tmp_path / "videos" / "scripts" / "scripts" / "2D" / "non_AI"
        for video, script in ((top, scripts / "clip.funscript"),
                              (nested, scripts / "batch" / "portrait" / "nested_clip.funscript")):
            _videos(video.parent, video.name)
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("{}", encoding="utf-8")

        videos = _the_list(tmp_path, str(root)).videos(
            MainListSettings(scripted_filter=True))

        assert set(videos) == {str(top), str(nested)}

    def test_it_keeps_a_kept_video_whose_hand_written_script_is_in_the_library(self, tmp_path):
        library = tmp_path / "library" / "videos"
        kept_dir = tmp_path / "cloud" / "videos" / "videos" / "VR" / "finished"
        kept, _unscripted = _videos(kept_dir, "scene one.mp4", "scene two.mp4")
        script = library / "scripts" / "scripts" / "VR" / "finished" / "scene one.funscript"
        script.parent.mkdir(parents=True)
        script.write_text("{}", encoding="utf-8")

        videos = _the_list(tmp_path, str(kept_dir), metadata_root=library / "metadata").videos(
            MainListSettings(scripted_filter=True))

        assert videos == [str(kept)]


class TestShapes:
    def test_it_narrows_to_the_shape_asked_for(self, tmp_path):
        vr_dir, flat_dir = tmp_path / "vr", tmp_path / "flat"
        _videos(vr_dir, "wrap.mp4")
        _videos(flat_dir, "screen.mp4")
        the_list = _the_list(tmp_path, f"{vr_dir}|{flat_dir}", vr_dirs=str(vr_dir))

        every = the_list.videos(MainListSettings())
        vr_only = the_list.videos(MainListSettings(plays_flat=False))

        assert len(every) == 2
        assert [Path(video).name for video in vr_only] == ["wrap.mp4"]

    def test_a_session_with_no_vr_library_narrows_nothing_whatever_was_left_on(self, tmp_path):
        root = tmp_path / "flat"
        _videos(root, "scene one.mp4", "scene two_180_sbs.mp4")

        videos = _the_list(tmp_path, str(root)).videos(
            MainListSettings(plays_vr=False))

        assert len(videos) == 2


class TestOrder:
    def test_a_shuffle_is_the_same_until_another_is_asked_for(self, tmp_path):
        root = tmp_path / "vids"
        _videos(root, *(f"scene {number}.mp4" for number in range(20)))
        the_list = _the_list(tmp_path, str(root))

        first = the_list.videos(MainListSettings(shuffle_seed=7))
        again = the_list.videos(MainListSettings(shuffle_seed=7))

        assert again == first
        assert first != sorted(first)

    def test_another_shuffle_is_another_order(self, tmp_path):
        root = tmp_path / "vids"
        _videos(root, *(f"scene {number}.mp4" for number in range(20)))
        the_list = _the_list(tmp_path, str(root))

        assert (the_list.videos(MainListSettings(shuffle_seed=7))
                != the_list.videos(MainListSettings(shuffle_seed=8)))

    def test_a_video_that_arrives_takes_its_place_without_moving_the_others(self, tmp_path):
        root = tmp_path / "vids"
        _videos(root, *(f"scene {number}.mp4" for number in range(20)))
        the_list = _the_list(tmp_path, str(root))
        before = the_list.videos(MainListSettings(shuffle_seed=7))

        (arrived,) = _videos(root, "scene twenty.mp4")
        after = the_list.videos(MainListSettings(shuffle_seed=7))

        assert [video for video in after if video != str(arrived)] == before

    def test_latest_is_newest_first(self, tmp_path):
        root = tmp_path / "vids"
        old, new = _videos(root, "old.mp4", "new.mp4")
        os.utime(old, (1000, 1000))
        os.utime(new, (2000, 2000))

        videos = _the_list(tmp_path, str(root)).videos(MainListSettings(latest=True))

        assert videos == [str(new), str(old)]


class TestWhereItStarts:
    def _library(self, tmp_path) -> MainList:
        root = tmp_path / "vids"
        _videos(root / "vr", *(f"vr scene {number}.mp4" for number in range(10)))
        _videos(root / "flat", *(f"flat scene {number}.mp4" for number in range(10)))
        return _the_list(tmp_path, str(root), vr_dirs=str(root / "vr"))

    def test_at_the_video_on_screen_with_the_rest_after_it_in_order(self, tmp_path):
        the_list = self._library(tmp_path)
        whole = the_list.videos(MainListSettings(shuffle_seed=7))

        started = the_list.videos(MainListSettings(shuffle_seed=7), start_from=whole[5])

        assert started == whole[5:] + whole[:5]

    def test_at_the_next_video_let_through_when_the_one_on_screen_is_not(self, tmp_path):
        the_list = self._library(tmp_path)
        whole = the_list.videos(MainListSettings(shuffle_seed=7))
        on_screen = next(video for video in whole if "flat scene" in video)
        after_it = whole[whole.index(on_screen):] + whole[:whole.index(on_screen)]

        vr_only = the_list.videos(MainListSettings(plays_flat=False, shuffle_seed=7),
                                  start_from=on_screen)

        assert vr_only == [video for video in after_it if "vr scene" in video]

    def test_at_the_top_when_the_library_no_longer_holds_the_video(self, tmp_path):
        the_list = self._library(tmp_path)

        started = the_list.videos(MainListSettings(shuffle_seed=7),
                                  start_from=str(tmp_path / "vids" / "gone since.mp4"))

        assert started == the_list.videos(MainListSettings(shuffle_seed=7))


class TestWhenNothingIsLetThrough:
    def test_the_video_on_screen_is_held_as_the_whole_list(self, tmp_path):
        root = tmp_path / "vids"
        (on_screen,) = _videos(root / "flat", "scene one.mp4")
        the_list = _the_list(tmp_path, str(root), vr_dirs=str(root / "vr"))

        held = the_list.videos(MainListSettings(plays_vr=False, plays_flat=False),
                               start_from=str(on_screen))

        assert held == [str(on_screen)]

    def test_a_video_the_library_no_longer_holds_is_not(self, tmp_path):
        root = tmp_path / "vids"
        _videos(root / "flat", "scene one.mp4")
        the_list = _the_list(tmp_path, str(root), vr_dirs=str(root / "vr"))

        held = the_list.videos(MainListSettings(plays_vr=False, plays_flat=False),
                               start_from=str(root / "flat" / "gone since.mp4"))

        assert held == []


class TestWritingIt:
    def test_each_video_rides_with_the_funscript_beside_it(self, tmp_path):
        root = tmp_path / "videos" / "videos" / "primary"
        scripted, plain = _videos(root, "scripted.mp4", "plain.mp4")
        script = tmp_path / "videos" / "scripts" / "scripts" / "primary" / "scripted.funscript"
        script.parent.mkdir(parents=True)
        script.write_text("{}", encoding="utf-8")
        the_list = _the_list(tmp_path, str(root))

        the_list.write(MainListSettings())

        assert sorted(the_list.file.read_text(encoding="utf-8").splitlines()) == sorted(
            [f"{scripted}\t{script}", str(plain)])

    def test_it_is_the_file_the_main_funestra_reads(self, tmp_path):
        assert _the_list(tmp_path, "").file == tmp_path / "state" / "main_player_playlist.tsv"
