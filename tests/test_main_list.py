from __future__ import annotations

import json
import os
from pathlib import Path

from funestra.modes import LengthMode

from fun_time.main_list import MainList, MainListSettings, WrittenList
from fun_time.media_metadata import metadata_path_for
from fun_time.modes import build_generated_marker_path
from tests.drive_fakes import files_that_never_answer


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


class TestLength:
    """What Evolver recorded each video as decides the clips and full videos
    buttons; a video it has no record of counts as a full video."""

    def _library(self, tmp_path, kinds: dict[str, dict | None]) -> tuple[MainList, dict[str, str]]:
        root = tmp_path / "videos" / "videos" / "main"
        metadata_root = tmp_path / "videos" / "metadata"
        videos = {}
        for name, record in kinds.items():
            (video,) = _videos(root, f"{name}.mp4")
            videos[name] = str(video)
            if record is not None:
                sidecar = metadata_path_for(video, metadata_root)
                sidecar.parent.mkdir(parents=True, exist_ok=True)
                sidecar.write_text(json.dumps(record), encoding="utf-8")
        return _the_list(tmp_path, str(root), metadata_root=metadata_root), videos

    def _kinds(self, tmp_path):
        return self._library(tmp_path, {
            "short": {"video": {"type": "short"}},
            "excerpt": {"video": {"type": "excerpt"}},
            "carved": {"clip": {"compilation": "Vol6", "index": 1}},
            "whole": {"video": {"type": "full_length"}},
            "flick": {"video": {"type": "genau_flick"}},
            "unrecorded": None,
        })

    def test_clips_are_the_short_videos_and_the_ones_carved_from_longer_ones(self, tmp_path):
        the_list, videos = self._kinds(tmp_path)

        clips = the_list.videos(MainListSettings(length=LengthMode.CLIPS))

        assert sorted(clips) == sorted(videos[name] for name in ("short", "excerpt", "carved"))

    def test_full_videos_are_the_whole_ones_and_the_ones_evolver_has_no_record_of(self, tmp_path):
        the_list, videos = self._kinds(tmp_path)

        full = the_list.videos(MainListSettings(length=LengthMode.FULL))

        assert sorted(full) == sorted(videos[name] for name in ("whole", "unrecorded"))

    def test_both_lengths_hold_every_video(self, tmp_path):
        the_list, videos = self._kinds(tmp_path)

        assert sorted(the_list.videos(MainListSettings())) == sorted(videos.values())

    def test_neither_length_lets_no_video_through(self, tmp_path):
        the_list, videos = self._kinds(tmp_path)

        assert the_list.videos(MainListSettings(length=LengthMode.NONE),
                               on_screen=videos["whole"]) == []

    def test_without_a_record_to_read_every_video_is_a_full_video(self, tmp_path):
        root = tmp_path / "vids"
        (video,) = _videos(root, "scene one.mp4")
        the_list = _the_list(tmp_path, str(root))

        assert the_list.videos(MainListSettings(length=LengthMode.FULL)) == [str(video)]
        assert the_list.videos(MainListSettings(length=LengthMode.CLIPS)) == []

    def test_a_video_outside_the_library_the_records_mirror_is_a_full_video(self, tmp_path):
        root = tmp_path / "elsewhere"
        (video,) = _videos(root, "scene one.mp4")
        the_list = _the_list(tmp_path, str(root), metadata_root=tmp_path / "videos" / "metadata")

        assert the_list.videos(MainListSettings(length=LengthMode.FULL)) == [str(video)]

    def test_a_list_nobody_narrowed_holds_both_lengths(self):
        assert MainListSettings().length is LengthMode.MIXED

    def test_a_vr_video_is_a_full_video_and_no_question_is_asked_about_it(self, tmp_path):
        """The VR library lives on a drive that can take hours to answer about one
        file, and Evolver keeps no record of it to look for."""
        vr_dir = tmp_path / "VR"
        (vr_video,) = _videos(vr_dir, "scene one.mp4")
        the_list = _the_list(tmp_path, str(vr_dir), vr_dirs=str(vr_dir),
                             metadata_root=tmp_path / "videos" / "metadata")

        with files_that_never_answer(vr_dir):
            full = the_list.videos(MainListSettings(length=LengthMode.FULL))
            clips = the_list.videos(MainListSettings(length=LengthMode.CLIPS))

        assert (full, clips) == ([str(vr_video)], [])


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

        started = the_list.videos(MainListSettings(shuffle_seed=7), on_screen=whole[5])

        assert started == whole[5:] + whole[:5]

    def test_at_the_next_video_let_through_when_the_one_on_screen_is_not(self, tmp_path):
        the_list = self._library(tmp_path)
        whole = the_list.videos(MainListSettings(shuffle_seed=7))
        on_screen = next(video for video in whole if "flat scene" in video)
        after_it = whole[whole.index(on_screen):] + whole[:whole.index(on_screen)]

        vr_only = the_list.videos(MainListSettings(plays_flat=False, shuffle_seed=7),
                                  on_screen=on_screen)

        assert vr_only == [video for video in after_it if "vr scene" in video]

    def test_a_video_named_in_another_case_is_the_same_video(self, tmp_path):
        """The list and the Funestra's status are written by different processes,
        and Windows hands the same file back in either case."""
        the_list = self._library(tmp_path)
        whole = the_list.videos(MainListSettings(shuffle_seed=7))

        started = the_list.videos(MainListSettings(shuffle_seed=7), on_screen=whole[5].upper())

        assert started[0] == whole[5]

    def test_at_the_top_when_the_library_no_longer_holds_the_video(self, tmp_path):
        the_list = self._library(tmp_path)

        started = the_list.videos(MainListSettings(shuffle_seed=7),
                                  on_screen=str(tmp_path / "vids" / "gone since.mp4"))

        assert started == the_list.videos(MainListSettings(shuffle_seed=7))


class TestWhenNothingIsLetThrough:
    """However the buttons got there, an empty list is written as the video on
    screen alone, and said to be empty, for the press that emptied it to answer."""

    def _library(self, tmp_path):
        root = tmp_path / "vids"
        (on_screen,) = _videos(root / "flat", "scene one.mp4")
        return _the_list(tmp_path, str(root), vr_dirs=str(root / "vr")), str(on_screen)

    def test_the_video_on_screen_is_held_as_the_whole_list(self, tmp_path):
        the_list, on_screen = self._library(tmp_path)

        written = the_list.write(MainListSettings(plays_vr=False, plays_flat=False),
                                 on_screen=on_screen)

        assert written == WrittenList([on_screen], nothing_left=True)
        assert the_list.file.read_text(encoding="utf-8").splitlines() == [on_screen]

    def test_it_is_held_for_a_list_asked_for_from_the_top_too(self, tmp_path):
        """Latest and Shuffle start their list from its top, not from the video
        on screen, and an empty one still holds that video."""
        the_list, on_screen = self._library(tmp_path)

        written = the_list.write(MainListSettings(plays_vr=False, plays_flat=False),
                                 on_screen=on_screen, from_the_top=True)

        assert written == WrittenList([on_screen], nothing_left=True)

    def test_a_video_the_library_no_longer_holds_is_not(self, tmp_path):
        the_list, on_screen = self._library(tmp_path)

        written = the_list.write(MainListSettings(plays_vr=False, plays_flat=False),
                                 on_screen=str(Path(on_screen).with_name("gone since.mp4")))

        assert written == WrittenList([], nothing_left=True)

    def test_a_list_with_videos_in_it_is_not_empty(self, tmp_path):
        the_list, on_screen = self._library(tmp_path)

        assert the_list.write(MainListSettings(), on_screen=on_screen) == WrittenList(
            [on_screen], nothing_left=False)


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
