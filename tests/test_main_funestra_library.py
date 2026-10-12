from __future__ import annotations

from pathlib import Path

from funestra.playlist import PlaylistItem

from main_funestra.library import (
    LibraryEntry,
    VersionGroup,
    collapse_playlist_versions,
    group_versions,
    normalize_title,
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
        assert normalize_title("example_video-7c4d9e21_topaz") == "example video"


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
