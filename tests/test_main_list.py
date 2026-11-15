from __future__ import annotations

import json
import os
from pathlib import Path

from funestra.modes import LengthMode

from fun_time.main_list import MainList, MainListSettings, WrittenList
from fun_time.media_metadata import metadata_path_for
from tests.drive_fakes import files_that_never_answer


def _videos(folder: Path, *names: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    videos = [folder / name for name in names]
    for video in videos:
        video.write_text("x", encoding="utf-8")
    return videos


def _the_list(tmp_path: Path, sources: str, **where) -> MainList:
    return MainList(sources=sources, state_dir=tmp_path / "state", **where)


def _recorded_library(tmp_path, records: dict[str, dict | None]) -> tuple[MainList, dict[str, str]]:
    root = tmp_path / "videos" / "videos" / "main"
    metadata_root = tmp_path / "videos" / "metadata"
    videos = {}
    for name, record in records.items():
        (video,) = _videos(root, f"{name}.mp4")
        videos[name] = str(video)
        if record is not None:
            sidecar = metadata_path_for(video, metadata_root)
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            sidecar.write_text(json.dumps(record), encoding="utf-8")
    return _the_list(tmp_path, str(root), metadata_root=metadata_root), videos


class TestEnhanceds:
    def _library(self, tmp_path: Path) -> tuple[MainList, dict[str, Path]]:
        root = tmp_path / "vids"
        scene, upscale, lone = _videos(root, "Jane Doe - scene one.mp4",
                                       "Jane Doe - scene one_topaz.mp4", "Jane Doe - scene two.mp4")
        return _the_list(tmp_path, str(root)), {"scene": scene, "upscale": upscale, "lone": lone}

    def test_on_it_keeps_the_family_that_has_an_upscale(self, tmp_path):
        the_list, videos = self._library(tmp_path)

        kept = the_list.videos(MainListSettings(enhanced=True))

        assert sorted(kept) == sorted([str(videos["scene"]), str(videos["upscale"])])

    def test_off_is_the_default_and_keeps_everything(self, tmp_path):
        the_list, _videos = self._library(tmp_path)

        assert MainListSettings().enhanced is False
        assert len(the_list.videos(MainListSettings())) == 3


class TestFavorites:
    def test_on_it_keeps_only_the_videos_marked_a_favorite(self, tmp_path):
        the_list, videos = _recorded_library(tmp_path, {
            "liked": {"favorite": True}, "passed over": {"favorite": False}, "never seen": None})

        kept = the_list.videos(MainListSettings(favorites=True))

        assert kept == [videos["liked"]]

    def test_off_is_the_default_and_keeps_everything(self, tmp_path):
        the_list, _videos = _recorded_library(tmp_path, {
            "liked": {"favorite": True}, "never seen": None})

        assert MainListSettings().favorites is False
        assert len(the_list.videos(MainListSettings())) == 2

    def test_with_enhanceds_it_keeps_only_the_favorites_with_an_upscale(self, tmp_path):
        the_list, videos = _recorded_library(tmp_path, {
            "Jane Doe - scene one": {"favorite": True}, "Jane Doe - scene one_topaz": None,
            "Jane Doe - scene two": {"favorite": True}})

        kept = the_list.videos(MainListSettings(favorites=True, enhanced=True))

        assert kept == [videos["Jane Doe - scene one"]]


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
    def _kinds(self, tmp_path):
        return _recorded_library(tmp_path, {
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
        vr_dir = tmp_path / "VR"
        (vr_video,) = _videos(vr_dir, "scene one.mp4")
        the_list = _the_list(tmp_path, str(vr_dir), vr_dirs=str(vr_dir),
                             metadata_root=tmp_path / "videos" / "metadata")

        with files_that_never_answer(vr_dir):
            full = the_list.videos(MainListSettings(length=LengthMode.FULL))
            clips = the_list.videos(MainListSettings(length=LengthMode.CLIPS))

        assert (full, clips) == ([str(vr_video)], [])


class TestOrientation:
    def _turned(self, tmp_path):
        return _recorded_library(tmp_path, {
            "tall": {"video": {"orientation": "portrait", "type": "short"}},
            "tall whole": {"video": {"orientation": "portrait", "type": "full_length"}},
            "wide": {"video": {"orientation": "landscape", "type": "short"}},
            "unmeasured": None,
        })

    def test_portrait_only_holds_the_videos_measured_portrait(self, tmp_path):
        the_list, videos = self._turned(tmp_path)

        kept = the_list.videos(MainListSettings(plays_landscape=False))

        assert sorted(kept) == sorted(videos[name] for name in ("tall", "tall whole"))

    def test_landscape_only_holds_the_rest_and_the_unmeasured(self, tmp_path):
        the_list, videos = self._turned(tmp_path)

        kept = the_list.videos(MainListSettings(plays_portrait=False))

        assert sorted(kept) == sorted(videos[name] for name in ("wide", "unmeasured"))

    def test_neither_orientation_lets_no_video_through(self, tmp_path):
        the_list, _videos_named = self._turned(tmp_path)

        assert the_list.videos(MainListSettings(plays_portrait=False,
                                                plays_landscape=False)) == []

    def test_an_orientation_and_a_length_narrow_together(self, tmp_path):
        the_list, videos = self._turned(tmp_path)

        kept = the_list.videos(MainListSettings(plays_landscape=False, length=LengthMode.CLIPS))

        assert kept == [videos["tall"]]

    def test_a_vr_video_is_landscape_and_no_question_is_asked_about_it(self, tmp_path):
        vr_dir = tmp_path / "VR"
        (vr_video,) = _videos(vr_dir, "scene one.mp4")
        the_list = _the_list(tmp_path, str(vr_dir), vr_dirs=str(vr_dir),
                             metadata_root=tmp_path / "videos" / "metadata")

        with files_that_never_answer(vr_dir):
            wide = the_list.videos(MainListSettings(plays_portrait=False))
            tall = the_list.videos(MainListSettings(plays_landscape=False))

        assert (wide, tall) == ([str(vr_video)], [])


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
