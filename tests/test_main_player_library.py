from __future__ import annotations

import random
from pathlib import Path

import pytest
from player_core.playlist import PlaylistItem

from main_player.library import (
    EXCERPT,
    FULL,
    FULL_LENGTH,
    MIXED,
    NONE,
    SHORT_MAX_S,
    LibraryEntry,
    VersionGroup,
    canonical_playlist,
    collapse_playlist_versions,
    group_versions,
    library_playlist,
    normalize_title,
    select_library,
    version_index_from_groups,
)


def _entry(name: str, size: int, funscript: str | None = None) -> LibraryEntry:
    return LibraryEntry(
        video=Path(name),
        funscript=Path(funscript) if funscript else None,
        size=size,
    )


class TestNormalizeTitle:
    def test_lowercases(self):
        assert normalize_title("Jane-Doe") == "jane doe"

    def test_strips_quality_and_upscaler_tokens(self):
        assert normalize_title("scene-three-upscale-mp4-1080p_60fps") == "scene three"
        assert normalize_title("Jane-Doe-&-John-Roe-old_iris2") == "jane doe & john roe"

    def test_strips_trailing_hash_tokens(self):
        # 6-12 char alnum tokens mixing letters+digits, when trailing.
        assert normalize_title("redacted_540-EhWGJW62") == "redacted"
        assert normalize_title("Jane-Doe-&-John-Roe-ab12cd34-old_iris2") == "jane doe & john roe"
        # A trailing hash exposed only after a quality token is stripped.
        assert normalize_title("funscripted_video-0980a34b_topaz") == "funscripted video"


class TestGroupVersions:
    def test_singleton_group(self):
        groups = group_versions([_entry("solo.mp4", 100)])

        assert len(groups) == 1
        assert isinstance(groups[0], VersionGroup)
        assert groups[0].canonical.video == Path("solo.mp4")
        assert groups[0].alternates == []

    def test_largest_file_is_canonical(self):
        small = _entry("Jane-Doe-540.mp4", size=100)
        big = _entry("Jane-Doe-1080p.mp4", size=900)
        mid = _entry("Jane-Doe-720p.mp4", size=400)

        groups = group_versions([small, big, mid])

        assert len(groups) == 1
        assert groups[0].canonical is big
        assert [a.video for a in groups[0].alternates] == [
            Path("Jane-Doe-720p.mp4"),
            Path("Jane-Doe-540.mp4"),
        ]

    def test_distinct_titles_stay_separate(self):
        groups = group_versions([
            _entry("alpha-1080p.mp4", 100),
            _entry("beta-1080p.mp4", 100),
        ])

        assert [g.canonical.video for g in groups] == [
            Path("alpha-1080p.mp4"),
            Path("beta-1080p.mp4"),
        ]

    def test_scripted_and_unscripted_alternate_group_together(self):
        scripted = _entry("John-Roe-topaz.mp4", size=500, funscript="John-Roe.funscript")
        unscripted = _entry("John-Roe-1080p.mp4", size=800)

        groups = group_versions([scripted, unscripted])

        assert len(groups) == 1
        assert groups[0].canonical is unscripted  # larger
        assert groups[0].alternates == [scripted]
        assert groups[0].alternates[0].funscript == Path("John-Roe.funscript")

    def test_upscale_with_appended_tag_folds_into_original(self):
        # The real-world miss: the upscale is the original's name plus an
        # appended tag, and the original's trailing hash is stripped while the
        # upscale keeps it mid-name — so exact-title matching split them. A
        # token-wise prefix match ("richard roe" begins the upscale) folds them.
        original = _entry("Richard-Roe-ab12cd34.mp4", size=166)
        upscale = _entry("Richard-Roe-ab12cd34_3_apf2_iris2.mp4", size=6035)

        groups = group_versions([original, upscale])

        assert len(groups) == 1
        assert groups[0].canonical is upscale  # larger
        assert groups[0].alternates == [original]

    def test_numbered_scenes_with_shared_prefix_stay_separate(self):
        # Same performer, different scene index — NOT versions of each other,
        # even though they share the first two tokens.
        one = _entry("Mary-Roe-1_1080-wxyzabcd.mp4", size=1043)
        two = _entry("Mary-Roe-2_720-mnpq12rs.mp4", size=347)

        groups = group_versions([one, two])

        assert len(groups) == 2

    def test_upscales_of_different_numbered_scenes_form_separate_pairs(self):
        # Several short scenes, each with an appended-tag upscale, must fold
        # into one pair per scene — not a single over-grouped blob (which the
        # old first-two-tokens + duration heuristic produced for same-length
        # clips of one performer).
        entries = []
        for n in (1, 2, 3):
            entries.append(_entry(f"clip-{n}.mp4", size=10))
            entries.append(_entry(f"clip-{n}_apo8_iris2.mp4", size=400))

        groups = group_versions(entries)

        assert len(groups) == 3
        assert all(len(g.entries) == 2 for g in groups)

    def test_bracketed_numbers_mark_different_scenes(self):
        # A downloader that meets the same title twice names the second copy
        # "(1)", the third "(2)".  Those are different scenes, so the plain
        # name must not anchor a group they all join -- but a tag appended to
        # one of them is still a version of THAT scene.
        plain = _entry("Jane Doe - Example Studio.mp4", size=100)
        second = _entry("Jane Doe - Example Studio (1).mp4", size=110)
        third = _entry("Jane Doe - Example Studio (2).mp4", size=120)
        third_upscale = _entry("Jane Doe - Example Studio (2)_topaz.mp4", size=900)

        groups = group_versions([plain, second, third, third_upscale])

        assert len(groups) == 3
        by_canonical = {g.canonical.video.name: g for g in groups}
        assert set(by_canonical) == {
            "Jane Doe - Example Studio.mp4",
            "Jane Doe - Example Studio (1).mp4",
            "Jane Doe - Example Studio (2)_topaz.mp4",
        }
        upscaled = by_canonical["Jane Doe - Example Studio (2)_topaz.mp4"]
        assert upscaled.alternates == [third]



class TestNoLengthAtAll:
    """Neither length: a browse with nothing in it, which the console offers
    rather than refusing the press."""

    def test_no_length_keeps_nothing(self):
        kept = select_library(
            [_entry("scene.mp4", 100)], mode=NONE, durations={Path("scene.mp4"): 600.0},
            genau_clips=[])

        assert kept == []


class TestRecordedFamiliesSplitByCopyIndex:
    """Evolver records one family id for every file whose name begins the same
    way, so a downloaded set comes back as one family when it is several scenes.
    The id still anchors the family; the bracketed number refines it."""

    def _grouped(self, names):
        ids = dict.fromkeys(names, "Jane Doe - Example Studio")
        entries = [_entry(name, size=100 + i) for i, name in enumerate(names)]
        return group_versions(entries, lambda video: ids.get(video.name))

    def test_one_recorded_family_becomes_one_group_per_numbered_scene(self):
        groups = self._grouped([
            "Jane Doe - Example Studio.mp4",
            "Jane Doe - Example Studio (2).mp4",
            "Jane Doe - Example Studio (3).mp4",
        ])

        assert len(groups) == 3

    def test_a_scenes_own_upscale_stays_with_it(self):
        groups = self._grouped([
            "Jane Doe - Example Studio (2).mp4",
            "Jane Doe - Example Studio (2)_topaz.mp4",
            "Jane Doe - Example Studio (3).mp4",
        ])

        assert len(groups) == 2
        paired = next(g for g in groups if len(g.entries) == 2)
        assert {e.video.name for e in paired.entries} == {
            "Jane Doe - Example Studio (2).mp4",
            "Jane Doe - Example Studio (2)_topaz.mp4",
        }

    def test_a_family_with_no_numbers_in_it_is_untouched(self):
        """A hand-renamed re-encode is exactly what the recorded id is for, and
        nothing here may come between it and its original."""
        groups = self._grouped([
            "Jane Doe - Example Studio.mp4",
            "a totally different name.mp4",
        ])

        assert len(groups) == 1


class TestCanonicalPlaylist:
    def test_one_canonical_entry_per_group(self):
        entries = [
            _entry("Jane-Doe-540.mp4", 100),
            _entry("Jane-Doe-1080p.mp4", 900),  # canonical of its group
            _entry("John-Roe-720p.mp4", 200),  # sole entry
        ]

        playlist = canonical_playlist(entries, random.Random(0))

        videos = {e.video for e in playlist}
        assert videos == {Path("Jane-Doe-1080p.mp4"), Path("John-Roe-720p.mp4")}
        assert len(playlist) == 2

    def test_deterministic_given_seeded_rng(self):
        entries = [_entry(f"vid-{i}-1080p.mp4", 100) for i in range(8)]

        first = canonical_playlist(entries, random.Random(42))
        second = canonical_playlist(entries, random.Random(42))

        assert [e.video for e in first] == [e.video for e in second]

    def test_every_video_in_the_library_comes_back_as_an_entry(self):
        playlist = canonical_playlist([_entry("solo-1080p.mp4", 100)], random.Random(1))

        assert isinstance(playlist[0], LibraryEntry)


class TestSelectLibrary:
    def _durations(self, mapping):
        return {Path(k): v for k, v in mapping.items()}

    def test_full_length_mode_keeps_only_long_videos(self):
        entries = [
            _entry("long-1080p.mp4", 100),
            _entry("clip-1080p.mp4", 100),
        ]
        durations = self._durations({"long-1080p.mp4": 300.0, "clip-1080p.mp4": 6.0})

        result = select_library(entries, mode="full", durations=durations, genau_clips=[])

        assert [e.video for e in result] == [Path("long-1080p.mp4")]

    def test_shorts_mode_keeps_only_short_videos(self):
        entries = [
            _entry("long-1080p.mp4", 100),
            _entry("clip-1080p.mp4", 100),
        ]
        durations = self._durations({"long-1080p.mp4": 300.0, "clip-1080p.mp4": 6.0})

        result = select_library(entries, mode="shorts", durations=durations, genau_clips=[])

        assert [e.video for e in result] == [Path("clip-1080p.mp4")]

    def test_mixed_mode_keeps_everything(self):
        """The mode the player opens in: no length filter at all, which is what
        Fun Time's own playlist has always been."""
        entries = [_entry("long-1080p.mp4", 100), _entry("clip-1080p.mp4", 100)]
        durations = self._durations({"long-1080p.mp4": 300.0, "clip-1080p.mp4": 6.0})
        saved = [_entry("saved-clip.mp4", 100)]

        result = select_library(entries, mode=MIXED, durations=durations, genau_clips=saved)

        assert [e.video for e in result] == [
            Path("long-1080p.mp4"), Path("clip-1080p.mp4"), Path("saved-clip.mp4"),
        ]

    def test_mixed_mode_keeps_a_video_whose_duration_never_probed(self):
        """Length modes drop what they cannot classify; mixed classifies nothing,
        so an unprobed video is still playable."""
        entries = [_entry("unprobed-1080p.mp4", 100)]

        assert select_library(entries, mode=MIXED, durations={}, genau_clips=[])
        assert not select_library(entries, mode="shorts", durations={}, genau_clips=[])
        assert not select_library(entries, mode="full", durations={}, genau_clips=[])

    def test_the_boundary_second_is_short(self):
        entries = [_entry("exactly-at-the-line-1080p.mp4", 100)]
        durations = self._durations({"exactly-at-the-line-1080p.mp4": SHORT_MAX_S})

        assert select_library(entries, mode="shorts", durations=durations, genau_clips=[])
        assert not select_library(entries, mode="full", durations=durations, genau_clips=[])

    def test_long_compilation_is_not_a_short(self):
        # Duration-driven only: a "compilation" name doesn't make it short.
        entries = [_entry("mega-compilation-1080p.mp4", 100)]
        durations = self._durations({"mega-compilation-1080p.mp4": 1800.0})

        assert not select_library(entries, mode="shorts", durations=durations, genau_clips=[])
        assert select_library(entries, mode="full", durations=durations, genau_clips=[])

    def test_shorts_mode_includes_clips(self):
        entries = [_entry("long-1080p.mp4", 100)]
        clips = [_entry("saved-clip.mp4", 50)]
        durations = self._durations({"long-1080p.mp4": 300.0})

        result = select_library(entries, mode="shorts", durations=durations, genau_clips=clips)

        assert Path("saved-clip.mp4") in {e.video for e in result}

    def test_full_length_mode_excludes_clips(self):
        entries = [_entry("long-1080p.mp4", 100)]
        clips = [_entry("saved-clip.mp4", 50)]
        durations = self._durations({"long-1080p.mp4": 300.0})

        result = select_library(entries, mode="full", durations=durations, genau_clips=clips)

        assert Path("saved-clip.mp4") not in {e.video for e in result}

    def test_empty_clips_shorts_mode_is_duration_only(self):
        entries = [_entry("clip-1080p.mp4", 100)]
        durations = self._durations({"clip-1080p.mp4": 10.0})

        result = select_library(entries, mode="shorts", durations=durations, genau_clips=[])

        assert [e.video for e in result] == [Path("clip-1080p.mp4")]

    def test_missing_duration_excluded_from_length_filter(self):
        # An unprobed video (no duration) can't be classified; leave it out.
        entries = [_entry("unknown-1080p.mp4", 100)]

        assert select_library(entries, mode="full", durations={}, genau_clips=[]) == []
        assert select_library(entries, mode="shorts", durations={}, genau_clips=[]) == []

    def test_the_recorded_kind_decides_before_any_running_time(self):
        """Evolver settles this for the whole library; the player reads it."""
        entries = [_entry("carved-scene-1080p.mp4", 100)]
        durations = self._durations({"carved-scene-1080p.mp4": 300.0})
        kinds = {Path("carved-scene-1080p.mp4"): EXCERPT}

        assert select_library(entries, mode="shorts", durations=durations, genau_clips=[],
                              kind_of=kinds.get)
        assert not select_library(entries, mode="full", durations=durations, genau_clips=[],
                                  kind_of=kinds.get)

    def test_a_long_scene_recorded_as_full_length_is_never_measured(self):
        entries = [_entry("whole-scene-1080p.mp4", 100)]
        kinds = {Path("whole-scene-1080p.mp4"): FULL_LENGTH}

        assert select_library(entries, mode="full", durations={}, genau_clips=[],
                              kind_of=kinds.get)
        assert not select_library(entries, mode="shorts", durations={}, genau_clips=[],
                                  kind_of=kinds.get)

    def test_a_video_evolver_has_not_reached_falls_back_to_its_running_time(self):
        entries = [_entry("unrecorded-1080p.mp4", 100)]
        durations = self._durations({"unrecorded-1080p.mp4": 4.0})

        assert select_library(entries, mode="shorts", durations=durations, genau_clips=[],
                              kind_of=lambda _video: "")

    def test_a_genau_loop_is_a_short_whether_or_not_its_record_says_so(self):
        """The folder it was delivered to is the fallback for the loops."""
        clips = [_entry("saved-clip.mp4", 50)]

        kept = select_library([], mode="shorts", durations={}, genau_clips=clips,
                              kind_of=lambda _video: "")

        assert [e.video for e in kept] == [Path("saved-clip.mp4")]

    def test_applies_version_dedup(self):
        entries = [
            _entry("Jane-540.mp4", 100),
            _entry("Jane-1080p.mp4", 900),
        ]
        durations = self._durations({"Jane-540.mp4": 300.0, "Jane-1080p.mp4": 300.0})

        result = select_library(entries, mode="full", durations=durations, genau_clips=[])

        assert [e.video for e in result] == [Path("Jane-1080p.mp4")]


class TestLibraryPlaylist:
    def _durations(self, mapping):
        return {Path(k): v for k, v in mapping.items()}

    def test_full_length_pairs_are_deduped_and_shuffled(self):
        entries = [
            _entry("Jane-540.mp4", 100, funscript="Jane.funscript"),
            _entry("Jane-1080p.mp4", 900),  # canonical (bigger), no funscript
            _entry("John-720p.mp4", 200, funscript="John.funscript"),
        ]
        durations = self._durations({
            "Jane-540.mp4": 300.0, "Jane-1080p.mp4": 300.0, "John-720p.mp4": 300.0,
        })

        pairs = library_playlist(
            entries, mode="full", durations=durations, genau_clips=[],
            rng=random.Random(0),
        )

        # One entry per title; canonical (largest) chosen; correct pairing.
        as_dict = dict(pairs)
        assert set(as_dict) == {Path("Jane-1080p.mp4"), Path("John-720p.mp4")}
        assert as_dict[Path("Jane-1080p.mp4")] is None
        assert as_dict[Path("John-720p.mp4")] == Path("John.funscript")

    def test_each_entry_carries_its_video_and_its_funscript(self):
        entries = [_entry("solo-1080p.mp4", 100, funscript="solo.funscript")]
        durations = self._durations({"solo-1080p.mp4": 300.0})

        pairs = library_playlist(
            entries, mode="full", durations=durations, genau_clips=[],
            rng=random.Random(0),
        )

        assert pairs == [(Path("solo-1080p.mp4"), Path("solo.funscript"))]

    def test_deterministic_given_seed(self):
        entries = [_entry(f"v{i}-1080p.mp4", 100) for i in range(6)]
        durations = self._durations({f"v{i}-1080p.mp4": 300.0 for i in range(6)})

        a = library_playlist(entries, mode="full", durations=durations, genau_clips=[], rng=random.Random(3))
        b = library_playlist(entries, mode="full", durations=durations, genau_clips=[], rng=random.Random(3))
        assert a == b


class TestVersionIndex:
    def test_maps_every_member_to_ordered_group_pairs(self):
        big = _entry("Jane-1080p.mp4", 900)
        mid = _entry("Jane-720p.mp4", 400, funscript="Jane.funscript")
        solo = _entry("John-1080p.mp4", 100)
        groups = group_versions([mid, big, solo])

        index = version_index_from_groups(groups)

        jane_pairs = [(Path("Jane-1080p.mp4"), None), (Path("Jane-720p.mp4"), Path("Jane.funscript"))]
        assert index[Path("Jane-1080p.mp4")] == jane_pairs
        assert index[Path("Jane-720p.mp4")] == jane_pairs
        assert index[Path("John-1080p.mp4")] == [(Path("John-1080p.mp4"), None)]

    def test_singleton_maps_to_itself_only(self):
        groups = group_versions([_entry("solo-1080p.mp4", 100)])

        index = version_index_from_groups(groups)

        assert index == {Path("solo-1080p.mp4"): [(Path("solo-1080p.mp4"), None)]}


class TestEveryVideoIsServed:
    def test_no_flag_can_narrow_the_library_to_scripted_videos(self):
        """The main player standalone is a general player; scripted-focus is Fun Time's
        F-mode. A scripted_only flag threaded through four signatures drove a
        real filter that no production path ever turned on, and its own two
        docstrings disagreed about what it meant."""
        with pytest.raises(TypeError):
            select_library([], mode=FULL, durations={}, genau_clips=[], scripted_only=True)

    def test_an_unscripted_video_is_kept(self):
        unscripted = _entry("Eff-1080p.mp4", size=900)

        kept = select_library(
            [unscripted], mode=FULL, durations={unscripted.video: 300.0}, genau_clips=[],
        )

        assert [e.video for e in kept] == [unscripted.video]


class TestRealWorldNameGrouping:
    def test_original_with_hash_and_appended_upscale_fold(self):
        # The upscale is the original's name — trailing hash and all — plus an
        # appended pipeline tag, so the original's normalized title is a clean
        # token prefix of the upscale's. This is the dominant real-world shape.
        original = _entry("redacted_560-XmbgwUF4.mp4", size=289_000_000)
        upscale = _entry("redacted_560-XmbgwUF4_apo8_iris2.mp4", size=31_822_000_000)

        groups = group_versions([original, upscale])

        assert len(groups) == 1
        assert groups[0].canonical is upscale  # larger file
        assert groups[0].alternates == [original]

    def test_different_scene_names_stay_separate(self):
        a = _entry("Jane-Doe-scene-a.mp4", size=100)
        b = _entry("jane-doe-scene-b-1080p.mp4", size=100)

        groups = group_versions([a, b])

        assert len(groups) == 2


class TestCollapsePlaylistVersions:
    def _index(self, entries):
        return version_index_from_groups(group_versions(entries))

    def test_keeps_one_slot_per_group_at_first_seen_position(self):
        # A rotation listing both original and upscale collapses to the larger,
        # at the first-seen position, dropping the later duplicate.
        index = self._index([_entry("Richard.mp4", 166), _entry("Richard_topaz.mp4", 6035)])
        items = [
            PlaylistItem(Path("Richard.mp4")),
            PlaylistItem(Path("Other.mp4")),
            PlaylistItem(Path("Richard_topaz.mp4")),
        ]

        result = collapse_playlist_versions(items, index)

        assert result == [(Path("Richard_topaz.mp4"), None), (Path("Other.mp4"), None)]

    def test_keeps_the_funscript_of_the_entry_it_keeps(self):
        index = self._index([_entry("Richard.mp4", 166), _entry("Richard_topaz.mp4", 6035)])
        items = [
            PlaylistItem(Path("Richard_topaz.mp4"), Path("Richard_topaz.funscript")),
            PlaylistItem(Path("Richard.mp4"), Path("Richard.funscript")),
        ]

        result = collapse_playlist_versions(items, index)

        assert result == [(Path("Richard_topaz.mp4"), Path("Richard_topaz.funscript"))]

    def test_keeps_only_present_member_when_larger_absent(self):
        # F-mode may have filtered the upscale out; keep whatever version is here.
        index = self._index([_entry("Richard.mp4", 166), _entry("Richard_topaz.mp4", 6035)])
        items = [PlaylistItem(Path("Richard.mp4"), Path("Richard.funscript"))]

        result = collapse_playlist_versions(items, index)

        assert result == [(Path("Richard.mp4"), Path("Richard.funscript"))]

    def test_video_absent_from_index_passes_through(self):
        result = collapse_playlist_versions([PlaylistItem(Path("mystery.mp4"))], {})

        assert result == [(Path("mystery.mp4"), None)]


class TestGroupVersionsByRecordedId:
    def test_recorded_id_folds_clips_names_never_would(self):
        a = _entry("Totally-Different-A.mp4", 100)
        b = _entry("unrelated_name_b.mp4", 900)
        ids = {a.video: "fam1", b.video: "fam1"}

        groups = group_versions([a, b], ids.get)

        assert len(groups) == 1
        assert groups[0].canonical is b  # larger
        assert groups[0].alternates == [a]

    def test_clips_without_a_recorded_id_fall_back_to_name_grouping(self):
        rec_a = _entry("scene.mp4", 100)
        rec_b = _entry("other.mp4", 100)
        richard = _entry("Richard.mp4", 50)
        richard_up = _entry("Richard_topaz.mp4", 800)
        ids = {rec_a.video: "f", rec_b.video: "f"}  # these two have no sidecar

        groups = group_versions([rec_a, rec_b, richard, richard_up], ids.get)

        entry_sets = [sorted(str(m.video) for m in g.entries) for g in groups]
        assert sorted([str(rec_a.video), str(rec_b.video)]) in entry_sets
        assert sorted([str(richard.video), str(richard_up.video)]) in entry_sets
        assert len(groups) == 2
