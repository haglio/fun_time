from __future__ import annotations

import json
from pathlib import Path

from main_player.clip_nav import ClipNav


def _sidecar(lib: Path, meta: Path, rel: str, payload: dict) -> Path:
    v = lib / rel
    v.parent.mkdir(parents=True, exist_ok=True)
    v.write_bytes(b"x")
    side = (meta / v.relative_to(lib)).with_suffix(".json")
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps(payload), encoding="utf-8")
    return v


def _clip(lib, meta, rel, comp, index, source, performer, **recorded):
    return _sidecar(lib, meta, rel, {
        "video": {"action": "Alpha"},
        "clip": {"compilation": comp, "index": index, "source": source,
                 "performer": performer, **recorded},
    })


class TestClipNav:
    def _nav(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        clips = [
            _clip(lib, meta, "w/Ann Bly - Alpha Scene 2.mp4", "Vol6", 9, "Alpha Scene 2", "Ann Bly"),
            _clip(lib, meta, "w/Jane Doe - Scene Two.mp4", "Vol6", 1, "Scene Two", "Jane Doe"),
            _clip(lib, meta, "w/Ada Roe - Alpha Scene 2.mp4", "Vol6", 7, "Alpha Scene 2", "Ada Roe"),
            _clip(lib, meta, "w/Bryn Vance - Taylor Rain.mp4", "Vol10", 1, "Taylor Rain's Offroad Adventure", "Bryn Vance"),
        ]
        full = _sidecar(lib, meta, "other/Alpha Scene 2 - Ann Bly 1080p.mp4", {})
        nav = ClipNav.build([*clips, full], meta)
        return nav, clips, full

    def test_compilation_of_names_the_volume_a_clip_came_from(self, tmp_path):
        """The main player's HUD says which compilation is holding the playlist, so the title
        has to be reachable from the clip that put it there."""
        nav, clips, full = self._nav(tmp_path)

        assert nav.compilation_of(clips[0]) == "Vol6"
        assert nav.compilation_of(full) == ""

    def test_compilation_playlist_orders_by_index(self, tmp_path):
        nav, clips, _ = self._nav(tmp_path)
        # from Ann Bly (Vol6 #9): siblings Jane(#1), Ada(#7), Kim(#9)
        order = nav.compilation_playlist(clips[0])
        names = [p.stem.split(" - ")[0] for p in order]
        assert names == ["Jane Doe", "Ada Roe", "Ann Bly"]

    def test_compilation_playlist_excludes_other_volumes(self, tmp_path):
        nav, clips, _ = self._nav(tmp_path)
        order = nav.compilation_playlist(clips[1])  # Jane, Vol6
        assert all("Vol10" not in str(p) for p in order)
        assert len(order) == 3

    def test_compilation_playlist_empty_for_non_clip(self, tmp_path):
        nav, _, full = self._nav(tmp_path)
        assert nav.compilation_playlist(full) == []

    def test_full_vid_of_matches_source_and_performer(self, tmp_path):
        nav, clips, full = self._nav(tmp_path)
        # Ann Bly clip -> the "Alpha Scene 2 - Ann Bly" full scene
        assert nav.full_vid_of(clips[0]) == full

    def test_full_vid_of_none_when_absent(self, tmp_path):
        nav, clips, _ = self._nav(tmp_path)
        # Jane Doe / Scene Two has no matching full scene in the library
        assert nav.full_vid_of(clips[1]) is None

    def test_clip_of_reverse_matches(self, tmp_path):
        nav, clips, full = self._nav(tmp_path)
        assert nav.clip_of(full) == clips[0]

    def test_clip_of_ignores_a_performer_with_several_scenes(self, tmp_path):
        """A title like "Nora Quill To the Brink" must not claim every Nora Quill
        file: with several of her scenes present, which one it came from is
        unknowable, so neither direction may guess. (With exactly one it is not
        a guess — see the Iris Fenn case.)"""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        clip = _clip(lib, meta, "w/Nora Quill - Nora Quill To the Brink.mp4", "Vol1", 11,
                     "Nora Quill To the Brink", "Nora Quill")
        stranger = _sidecar(lib, meta, "other/Nora Quill - 9934197-720p.mp4", {})
        other = _sidecar(lib, meta, "other/Nora-Quill-2_1080-jx3sHGzf.mp4", {})
        nav = ClipNav.build([clip, stranger, other], meta)
        assert nav.clip_of(stranger) is None
        assert nav.full_vid_of(clip) is None

    def test_compilation_playlist_keeps_one_slot_per_scene(self, tmp_path):
        """An upscaled variant carries the same clip object; the playlist keeps the
        larger file once, not both versions of the scene."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        original = _clip(lib, meta, "w/Jane Doe - Scene Two.mp4", "Vol6", 1, "Scene Two", "Jane Doe")
        upscaled = _clip(lib, meta, "w/Jane Doe - Scene Two_apo8_iris2.mp4", "Vol6", 1, "Scene Two", "Jane Doe")
        upscaled.write_bytes(b"x" * 500)  # the enhanced file is the bigger one
        other = _clip(lib, meta, "w/Marlow Sterne - Alpha 1.mp4", "Vol6", 2, "Alpha Scene 1", "Marlow Sterne")
        nav = ClipNav.build([original, upscaled, other], meta)

        playlist = nav.compilation_playlist(original)

        assert playlist == [upscaled, other]

    def test_full_vid_resolves_on_performer_when_they_have_one_scene(self, tmp_path):
        """Most library files are named performer + resolution + hash, with no
        movie title at all — so a source-word match can never fire. One scene for
        that performer is unambiguous, so it resolves."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        clip = _clip(lib, meta, "w/Iris Fenn - Scene Five 3.mp4", "Vol4", 4,
                     "Scene Five 3", "Iris Fenn")
        scene = _sidecar(lib, meta, "other/Iris-Fenn_540-fDn1L7uT.mp4", {})
        nav = ClipNav.build([clip, scene], meta)

        assert nav.full_vid_of(clip) == scene

    def test_full_vid_stays_ambiguous_with_several_scenes_for_the_performer(self, tmp_path):
        """With several scenes by that performer there is no way to tell which one
        the clip came from, so it must not guess.

        Both scenes are built AND handed to the index: this test used to write
        one pair of sidecars and hand `build` a different pair, one of them a
        path nothing had created, so the performer had no candidate scenes at
        all and the None came from an empty list rather than from an ambiguous
        one.  The guard it is named for could be deleted with it still green.
        """
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        clip = _clip(lib, meta, "w/Nora Quill - Nora Quill To the Brink.mp4", "Vol1", 11,
                     "Nora Quill To the Brink", "Nora Quill")
        one = _sidecar(lib, meta, "other/Nora-Quill-1_540-aa11bb22.mp4", {})
        another = _sidecar(lib, meta, "other/Nora-Quill-2_1080-cc33dd44.mp4", {})
        nav = ClipNav.build([clip, one, another], meta)

        assert nav.full_vid_of(clip) is None

    def test_re_encodes_of_one_scene_are_not_ambiguous(self, tmp_path):
        """X.mp4 and X_iris2.mp4 are the same scene; the bigger one wins."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        clip = _clip(lib, meta, "w/Iris Fenn - Scene Five 3.mp4", "Vol4", 4,
                     "Scene Five 3", "Iris Fenn")
        plain = _sidecar(lib, meta, "other/Iris-Fenn_540-fDn1L7uT.mp4", {})
        upscaled = _sidecar(lib, meta, "other/Iris-Fenn_540-fDn1L7uT_iris2.mp4", {})
        upscaled.write_bytes(b"x" * 400)
        nav = ClipNav.build([clip, plain, upscaled], meta)

        assert nav.full_vid_of(clip) == upscaled

    def test_clip_jump_never_returns_the_file_you_are_on(self, tmp_path):
        """A clip matches its own name; returning it just replayed the video."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        clip = _clip(lib, meta, "w/Ann Bly - Alpha Scene 2.mp4", "Vol6", 9,
                     "Alpha Scene 2", "Ann Bly")
        nav = ClipNav.build([clip], meta)

        assert nav.clip_of(clip) is None

    def test_clip_of_none_for_unrelated(self, tmp_path):
        nav, clips, full = self._nav(tmp_path)
        lib = tmp_path / "videos" / "videos"
        stranger = _sidecar(lib, tmp_path / "videos" / "metadata", "other/Some Random Movie.mp4", {})
        nav2 = ClipNav.build([*clips, full, stranger], tmp_path / "videos" / "metadata")
        assert nav2.clip_of(stranger) is None


class TestRecordedMatch:
    """``full_video`` in the sidecar is a content-verified answer (Evolver's
    clip-match batch found the clip's frames inside that scene), so it outranks
    every guess the filename heuristic could make."""

    def test_full_vid_uses_the_recorded_scene(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        scene = _sidecar(lib, meta, "other/Nora-Quill-2_1080-jx3sHGzf.mp4", {})
        _sidecar(lib, meta, "other/redacted_540-EhWGJW62.mp4", {})
        clip = _clip(lib, meta, "w/Nora Quill - Nora Quill To the Brink.mp4", "Vol1", 11,
                     "Nora Quill To the Brink", "Nora Quill",
                     full_video=str(scene), scene_offset=808.2)
        nav = ClipNav.build([clip, scene, lib / "other" / "redacted_540-EhWGJW62.mp4"], meta)

        assert nav.full_vid_of(clip) == scene

    def test_clip_jump_uses_the_recorded_scene(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        scene = _sidecar(lib, meta, "other/Nora-Quill-2_1080-jx3sHGzf.mp4", {})
        mine = _clip(lib, meta, "w/Nora Quill - Nora Quill To the Brink.mp4", "Vol1", 11,
                     "Nora Quill To the Brink", "Nora Quill",
                     full_video=str(scene), scene_offset=808.2)
        other = _clip(lib, meta, "w/Nora Quill - Cockeyed.mp4", "Vol2", 3,
                      "Cockeyed", "Nora Quill",
                      full_video=str(lib / "other" / "redacted_540-EhWGJW62.mp4"),
                      scene_offset=12.0)
        nav = ClipNav.build([mine, other, scene], meta)

        assert nav.clip_of(scene) == mine

    def test_the_match_holds_across_versions_of_the_scene(self, tmp_path):
        """The batch aligns against whichever version is cheapest to decode, and
        "cycle version" can put any of them on screen — so a match recorded on
        the original has to resolve from the upscale too, in both directions."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        plain = _sidecar(lib, meta, "other/Nora-Quill-1_540-izB4YKFa.mp4", {})
        upscale = _sidecar(lib, meta, "other/Nora-Quill-1_540-izB4YKFa_apo8_iris2.mp4", {})
        upscale.write_bytes(b"x" * 400)
        decoy = _sidecar(lib, meta, "other/Nora-Quill-2_1080-QQ7mnbEt.mp4", {})
        clip = _clip(lib, meta, "w/Nora Quill - Nights of Nonsense 8.mp4", "Vol1", 9,
                     "Nights of Nonsense 8", "Nora Quill",
                     full_video=str(plain), scene_offset=808.2)
        nav = ClipNav.build([clip, plain, upscale, decoy], meta)

        assert nav.clip_of(upscale) == clip
        assert nav.full_vid_of(clip) == upscale

    def test_full_vid_goes_to_the_best_version_evolver_families_together(self, tmp_path):
        """A version saved under a name of its own — a 4K upscale of the best
        eight minutes — shares nothing with the original's name, so only the
        recorded version family reunites them. "full vid" owes the same file the
        playlist would show, which is the biggest of that family."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        family = {"version": {"group": "redacted_540-pacI21CK"}}
        original = _sidecar(lib, meta, "other/redacted_540-pacI21CK.mp4", family)
        upscale = _sidecar(lib, meta, "other/redacted Beta Cut 4k 60fps.mp4", family)
        upscale.write_bytes(b"x" * 600)
        clip = _clip(lib, meta, "w/redacted - Scene Three 8.mp4", "Vol7", 4,
                     "Scene Three 8", "redacted",
                     full_video=str(original), scene_offset=1109.6)
        nav = ClipNav.build([clip, original, upscale], meta)

        assert nav.full_vid_of(clip) == upscale
        assert nav.clip_of(upscale) == clip


def test_a_scene_and_its_apo8_iris2_upscale_are_one_scene(tmp_path):
    """Evolver's own output suffix was not in the quality-token list, so a scene
    and its upscale counted as two candidates and the match declined."""
    lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
    clip = _clip(lib, meta, "w/Nora Quill - Nights of Nonsense 8.mp4", "Vol1", 9,
                 "Nights of Nonsense 8", "Nora Quill")
    plain = _sidecar(lib, meta, "other/Nora-Quill_540-izB4YKFa.mp4", {})
    upscale = _sidecar(lib, meta, "other/Nora-Quill_540-izB4YKFa_apo8_iris2.mp4", {})
    upscale.write_bytes(b"x" * 400)
    nav = ClipNav.build([clip, plain, upscale], meta)

    assert nav.full_vid_of(clip) == upscale
    assert nav.clip_of(plain) == clip


class TestAFileThatVanishedMidScan:
    def test_it_weighs_nothing_rather_than_ending_the_walk(self, tmp_path):
        """Biggest-is-canonical needs a size for every candidate, and the
        library is walked while Evolver is moving files through it -- so a path
        that has gone since the index was built must not raise, and must lose to
        anything still there."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        clip = _clip(lib, meta, "w/Iris Fenn - Scene Five 3.mp4", "Vol4", 4,
                     "Scene Five 3", "Iris Fenn")
        gone = _sidecar(lib, meta, "other/Iris-Fenn_540-fDn1L7uT.mp4", {})
        here = _sidecar(lib, meta, "other/Iris-Fenn_540-fDn1L7uT_iris2.mp4", {})
        here.write_bytes(b"x" * 400)
        nav = ClipNav.build([clip, gone, here], meta)
        gone.unlink()

        assert nav.full_vid_of(clip) == here


class TestTitles:
    def test_a_clip_is_called_by_the_pair_recorded_for_it(self, tmp_path):
        """A Windows filename cannot hold a colon, so the movie's own punctuation
        survives only in the record -- which is why the record names it and the
        name it was saved under does not."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        clip = _clip(lib, meta, "w/Jane Doe - Alpha Study Part Two.mp4", "Vol2", 3,
                     "Alpha Study: Part Two", "Jane Doe")
        nav = ClipNav.build([clip], meta)

        assert nav.title_of(clip) == "Jane Doe - Alpha Study: Part Two"

    def test_a_scene_takes_the_name_of_the_clip_cut_from_it(self, tmp_path):
        """The library saves a scene under whatever the download called it; the
        clip carved from it is the only thing that knows the movie and who is in
        it, so the scene is named after its clip."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        scene = _sidecar(lib, meta, "other/Jane-Doe_540-hQ2vLm8t.mp4", {})
        clip = _clip(lib, meta, "w/Jane Doe - Alpha Study 3.mp4", "Vol2", 3,
                     "Alpha Study 3", "Jane Doe", full_video=str(scene))
        nav = ClipNav.build([clip, scene], meta)

        assert nav.title_of(scene) == "Jane Doe - Alpha Study 3"

    def test_a_video_no_clip_names_keeps_the_family_it_was_recorded_under(self, tmp_path):
        """Nothing here knows what the video is, so its name is all there is --
        but the family Evolver recorded is that name without the upscale tags
        the enhancer appended, which is the same name and less of a mouthful."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        scene = _sidecar(lib, meta, "other/Jane-Doe_540-hQ2vLm8t_apo8_iris2.mp4",
                         {"version": {"group": "Jane-Doe_540-hQ2vLm8t"}})
        nav = ClipNav.build([scene], meta)

        assert nav.title_of(scene) == "Jane-Doe_540-hQ2vLm8t"

    def test_a_video_with_nothing_recorded_at_all_keeps_its_filename(self, tmp_path):
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        scene = _sidecar(lib, meta, "other/Jane-Doe_540-hQ2vLm8t.mp4", {})
        nav = ClipNav.build([scene], meta)

        assert nav.title_of(scene) == "Jane-Doe_540-hQ2vLm8t"

    def test_half_a_record_still_names_the_scene_better_than_the_filename(self, tmp_path):
        """Not every clip was recorded with both halves, and the half there is
        beats a download's hash either way."""
        lib, meta = tmp_path / "videos" / "videos", tmp_path / "videos" / "metadata"
        performer_only = _clip(lib, meta, "w/Jane Doe - 1.mp4", "Vol2", 1, "", "Jane Doe")
        source_only = _clip(lib, meta, "w/Alpha Study 4.mp4", "Vol2", 2, "Alpha Study 4", "")
        neither = _clip(lib, meta, "w/hQ2vLm8t.mp4", "Vol2", 3, "", "")
        nav = ClipNav.build([performer_only, source_only, neither], meta)

        assert nav.title_of(performer_only) == "Jane Doe"
        assert nav.title_of(source_only) == "Alpha Study 4"
        assert nav.title_of(neither) == "hQ2vLm8t"
