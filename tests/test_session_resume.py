"""Bringing a reopened session back to the clip each player was on."""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path

from fun_time.session_resume import (
    RESUMED_FIELDS,
    playlist_fits_sources,
    playlist_opens_on,
    resume_main_loop,
    resume_playlists,
    resume_satellite_locks,
    resume_shared_state,
)
from fun_time.shared_state import BridgeState, read_shared_state, write_shared_state


def _clips(tmp_path: Path, *names: str) -> list[str]:
    """Real files on disk, since a playlist names videos that have to be there."""
    paths = []
    for name in names:
        clip = tmp_path / name
        clip.write_bytes(b"")
        paths.append(str(clip))
    return paths


def _write_playlist(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


class TestResumePlaylists:
    def test_rotates_a_playlist_onto_the_clip_that_was_on_screen(self, tmp_path: Path):
        """A player starts at the top of its playlist file, so putting last
        session's clip there is what makes it resume — and rotating (rather than
        moving it to the front) keeps the clips that were coming up coming up."""
        a, b, c = _clips(tmp_path, "a.mp4", "b.mp4", "c.mp4")
        playlist = tmp_path / "portrait_playlist.tsv"
        _write_playlist(playlist, [a, b, c])

        assert resume_playlists([(playlist, b)]) is True

        assert playlist.read_text(encoding="utf-8").splitlines() == [b, c, a]

    def test_carries_the_funscript_column_through_the_rotation(self, tmp_path: Path):
        """Nau's playlist pairs each video with the funscript that drives the
        OSR2 through it; rewriting the file without that column would leave a
        resumed session silently unscripted."""
        a, b, c = _clips(tmp_path, "a.mp4", "b.mp4", "c.mp4")
        playlist = tmp_path / "nau_playlist.tsv"
        _write_playlist(playlist, [f"{a}\ta.funscript", b, f"{c}\tc.funscript"])

        resume_playlists([(playlist, c)])

        assert playlist.read_text(encoding="utf-8").splitlines() == [
            f"{c}\tc.funscript",
            f"{a}\ta.funscript",
            b,
        ]

    def test_reports_nothing_to_resume_when_a_playlist_file_is_missing(self, tmp_path: Path):
        """A first run, or a state dir that has been wiped: there is no last
        session on disk, so the caller has to build the playlists fresh."""
        assert resume_playlists([(tmp_path / "portrait_playlist.tsv", "")]) is False

    def test_leaves_every_playlist_alone_when_one_of_them_cannot_be_resumed(self, tmp_path: Path):
        """One build writes all three playlists, so they live or die together:
        if any of them has no session to come back to, the caller rebuilds the
        set and each file must still be as the build left it."""
        a, b = _clips(tmp_path, "a.mp4", "b.mp4")
        portrait = tmp_path / "portrait_playlist.tsv"
        _write_playlist(portrait, [a, b])

        resumed = resume_playlists([
            (portrait, b),
            (tmp_path / "nau_playlist.tsv", ""),
        ])

        assert resumed is False
        assert portrait.read_text(encoding="utf-8").splitlines() == [a, b]

    def test_keeps_the_queue_when_the_clip_it_left_on_is_gone(self, tmp_path: Path):
        """A clip trashed or deleted since leaves nothing to rotate onto, but
        the queue around it is still last session's — worth far more than a
        fresh shuffle — so it comes back from its top."""
        a, b = _clips(tmp_path, "a.mp4", "b.mp4")
        playlist = tmp_path / "landscape_playlist.tsv"
        _write_playlist(playlist, [a, b])

        assert resume_playlists([(playlist, str(tmp_path / "deleted.mp4"))]) is True

        assert playlist.read_text(encoding="utf-8").splitlines() == [a, b]

    def test_drops_clips_that_no_longer_exist(self, tmp_path: Path):
        """A playlist built moments before launch could only name files that were
        there; one resumed from yesterday can name files trashed since, and mpv
        cannot load those — so they come out as the file is rewritten."""
        a, b = _clips(tmp_path, "a.mp4", "b.mp4")
        gone = str(tmp_path / "gone.mp4")
        playlist = tmp_path / "portrait_playlist.tsv"
        _write_playlist(playlist, [a, gone, b])

        assert resume_playlists([(playlist, b)]) is True

        assert playlist.read_text(encoding="utf-8").splitlines() == [b, a]


class TestResumeSharedState:
    """The other half of a resume: coming back in the mode you left in.

    The playlist files carry F-mode, each side's filter and order, and any group
    loop baked into them, so a session that resumed those files and opened on a
    default state described itself wrongly on every HUD — and answered the next
    "F-mode" by reporting it *enabled* while nothing visibly changed.
    """

    def test_carries_each_players_own_f_mode_onto_the_resumed_session(self, tmp_path: Path):
        """A resumed playlist holds whatever its own player was narrowed to, so
        each F-mode comes back as it was closed — and each HUD has to say so.
        Carrying one flag for all three would put players into an F-mode whose
        playlist was never built under it."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(
            main_f_mode=True, portrait_f_mode=False, landscape_f_mode=True))

        state = resume_shared_state(state_file, resumed=True)

        assert (state.main_f_mode, state.portrait_f_mode,
                state.landscape_f_mode) == (True, False, True)

    def test_carries_a_running_loop_and_the_map_it_hangs_on(self, tmp_path: Path):
        """A loop IS the group written out as the side's playlist, so resuming
        that file resumes the loop — the HUD has to keep its button lit and its
        map frozen where the loop left it, not read the queue as a browse."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(
            portrait_loop="seed",
            portrait_map_anchor="C:/v/a.mp4",
            portrait_widen_clip="C:/v/a.mp4",
            landscape_loop="action",
            landscape_map_anchor="C:/v/b.mp4",
        ))

        state = resume_shared_state(state_file, resumed=True)

        assert state.portrait_loop == "seed"
        assert state.portrait_map_anchor == "C:/v/a.mp4"
        assert state.portrait_widen_clip == "C:/v/a.mp4"
        assert state.landscape_loop == "action"
        assert state.landscape_map_anchor == "C:/v/b.mp4"

    def test_carries_each_side_s_filter_and_order(self, tmp_path: Path):
        """Both narrowed the playlist that just came back, so both are still in
        force and belong on the status line."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(
            portrait_filter="alpha", landscape_filter="beta gamma",
            portrait_latest=True, landscape_latest=True,
        ))

        state = resume_shared_state(state_file, resumed=True)

        assert (state.portrait_filter, state.landscape_filter) == ("alpha", "beta gamma")
        assert state.portrait_latest is True
        assert state.landscape_latest is True

    def test_carries_the_main_player_s_order(self, tmp_path: Path):
        """The main player's order fixes its resumed playlist exactly as a
        satellite's does, so it comes back with it — otherwise the session opens
        on a newest-first list while its HUD says Shuffle, and the next rebuild
        reshuffles the order it was left in."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(main_latest=True))

        state = resume_shared_state(state_file, resumed=True)

        assert state.main_latest is True

    def test_carries_the_sound_level_and_each_side_s_lock(self, tmp_path: Path):
        """Neither is a thing that should reset overnight, so both come back —
        and each has its world put back with it, the level seeded to both audio
        sinks at startup and the lock re-sent to its satellite."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(
            volume=40, muted=True, locked2=True, locked3=False,
        ))

        state = resume_shared_state(state_file, resumed=True)

        assert (state.volume, state.muted) == (40, True)
        assert (state.locked2, state.locked3) == (True, False)

    def test_carries_the_mode_the_primary_slot_was_left_in(self, tmp_path: Path):
        """Which player owns the big display is as much a thing you set as the
        sound level, so leaving the session showing Genau and reopening on Nau
        is the same overnight reset.  Startup builds every session in nau mode
        and then puts the carried mode on over the top (see
        :func:`fun_time.windows_bridge_startup.seed_startup_states`)."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(main_mode="genau"))

        assert resume_shared_state(state_file, resumed=True).main_mode == "genau"

    def test_drops_the_state_nothing_on_disk_brings_back(self, tmp_path: Path):
        """OmniPause's flags are cleared before the players launch, and a
        keyboard selection was never a thing you could leave running.  Carrying
        either forward would be the same lie in the other direction."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(
            omni_paused=True, active_side=3,
            portrait_nav_anchor="C:/v/a.mp4", landscape_nav_anchor="C:/v/b.mp4",
        ))

        state = resume_shared_state(state_file, resumed=True)

        fresh = BridgeState()
        for field in fields(BridgeState):
            if field.name not in RESUMED_FIELDS:
                assert getattr(state, field.name) == getattr(fresh, field.name)


class TestResumeSatelliteLocks:
    """A lock is repeat-one in mpv's own loop_file, so it dies with the player
    process and has to be re-sent — unlike a filter or a loop, which ride back in
    on the playlist file the new player reads."""

    def test_queues_a_lock_for_the_side_that_was_holding_one(self, tmp_path: Path):
        portrait = tmp_path / "portrait_cmd.txt"
        landscape = tmp_path / "landscape_cmd.txt"

        resume_satellite_locks([(portrait, True), (landscape, False)])

        assert portrait.read_text(encoding="utf-8").split() == ["LOCK"]
        assert not landscape.exists()

    def test_queues_nothing_for_a_session_that_was_not_locked(self, tmp_path: Path):
        """An unlocked side must not be sent anything at all: the satellite
        launches auto-advancing, which is already what unlocked means."""
        portrait = tmp_path / "portrait_cmd.txt"

        resume_satellite_locks([(portrait, False)])

        assert not portrait.exists()

    def test_keeps_whatever_is_already_queued(self, tmp_path: Path):
        """The command file is a queue, so the lock joins it rather than
        replacing a verb something else is waiting to have drained."""
        portrait = tmp_path / "portrait_cmd.txt"
        portrait.write_text("RELOAD_PLAYLIST\n", encoding="utf-8")

        resume_satellite_locks([(portrait, True)])

        assert portrait.read_text(encoding="utf-8").split() == ["RELOAD_PLAYLIST", "LOCK"]

    def test_opens_on_defaults_when_the_playlists_were_built_fresh(self, tmp_path: Path):
        """Nothing to resume means the builder just wrote three fresh playlists
        with F-mode off, so last session's state describes files that are gone."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(portrait_f_mode=True, portrait_loop="seed"))

        state = resume_shared_state(state_file, resumed=False)

        assert state == BridgeState()

    def test_the_seeded_state_is_what_the_session_opens_on(self, tmp_path: Path):
        """The dispatch loop reads its state off this file every tick and never
        hears about the return value, so the carry has to be on disk."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(portrait_f_mode=True, omni_paused=True))

        resume_shared_state(state_file, resumed=True)

        written = read_shared_state(state_file)
        assert written is not None
        assert written.portrait_f_mode is True
        assert written.omni_paused is False

    def test_a_first_run_leaves_a_state_file_behind(self, tmp_path: Path):
        """There is nothing to read on a wiped state dir, and the session still
        has to open on a state — a written default, not a missing file."""
        state_file = tmp_path / "shared_bridge_state.ini"

        assert resume_shared_state(state_file, resumed=True) == BridgeState()
        assert state_file.exists()


class TestResumeMainLoop:
    """The main player's A/B loop is a range inside one video, held in a player
    process that is about to be replaced — so like a satellite's lock it cannot
    ride back in on a file the new player reads, and has to be re-sent."""

    def test_queues_the_range_the_primary_was_looping(self, tmp_path: Path):
        nau_cmd = tmp_path / "nau_cmd.txt"

        resume_main_loop(nau_cmd, (2000, 4000))

        assert nau_cmd.read_text(encoding="utf-8").splitlines() == ["SET_LOOP 2000 4000"]

    def test_queues_nothing_when_there_was_no_loop(self, tmp_path: Path):
        """A main player that was not looping must be sent nothing at all: playing
        the video through is already what no loop means."""
        nau_cmd = tmp_path / "nau_cmd.txt"

        resume_main_loop(nau_cmd, None)

        assert not nau_cmd.exists()

    def test_keeps_whatever_is_already_queued(self, tmp_path: Path):
        """Startup has already seeded this channel with the sound level and the
        F-mode flag, and Nau has drained none of it yet."""
        nau_cmd = tmp_path / "nau_cmd.txt"
        nau_cmd.write_text("SET_VOLUME 40 0\n", encoding="utf-8")

        resume_main_loop(nau_cmd, (2000, 4000))

        assert nau_cmd.read_text(encoding="utf-8").splitlines() == [
            "SET_VOLUME 40 0", "SET_LOOP 2000 4000",
        ]


class TestPlaylistOpensOn:
    """Whether the clip a player will actually load is the one expected — asked
    of the main player before its loop is handed back, since a loop belongs to one
    video and would otherwise be applied to whatever took its place."""

    def test_the_clip_a_resumed_playlist_leads_with(self, tmp_path: Path):
        a, b = _clips(tmp_path, "a.mp4", "b.mp4")
        playlist = tmp_path / "nau_playlist.tsv"
        _write_playlist(playlist, [b, a])

        assert playlist_opens_on(playlist, b) is True
        assert playlist_opens_on(playlist, a) is False

    def test_case_alone_is_not_a_different_clip(self, tmp_path: Path):
        """The playlist and the status file are written by different processes,
        and Windows hands the same file back in either case."""
        playlist = tmp_path / "nau_playlist.tsv"
        _write_playlist(playlist, [str(tmp_path / "Sub" / "a.mp4")])

        assert playlist_opens_on(playlist, str(tmp_path / "sub" / "A.MP4")) is True

    def test_a_funscript_column_does_not_hide_the_video(self, tmp_path: Path):
        a, b = _clips(tmp_path, "a.mp4", "b.mp4")
        playlist = tmp_path / "nau_playlist.tsv"
        _write_playlist(playlist, [f"{a}\ta.funscript", b])

        assert playlist_opens_on(playlist, a) is True

    def test_an_empty_playlist_opens_on_nothing(self, tmp_path: Path):
        assert playlist_opens_on(tmp_path / "absent.tsv", "C:/v/a.mp4") is False


class TestPlaylistFitsSources:
    """Telling a playlist this session built from one another app left behind.

    FunTimeVR shares this state dir and writes the main player's playlist to the
    same file, built from the VR library merged with the desktop's — so the
    desktop session has to recognize that file rather than resume it.
    """

    def test_a_playlist_from_the_session_s_own_library_fits(self, tmp_path: Path):
        library = tmp_path / "library" / "2D"
        library.mkdir(parents=True)
        playlist = tmp_path / "nau_playlist.tsv"
        _write_playlist(playlist, [
            str(library / "scene one.mp4"),
            str(library / "deeper" / "scene two.mp4"),
        ])

        assert playlist_fits_sources(playlist, str(library)) is True

    def test_one_video_from_another_library_is_enough_not_to_fit(self, tmp_path: Path):
        library = tmp_path / "library" / "2D"
        elsewhere = tmp_path / "library" / "VR" / "finished"
        playlist = tmp_path / "nau_playlist.tsv"
        _write_playlist(playlist, [
            str(library / "scene one.mp4"),
            f"{elsewhere / 'headset scene.mp4'}\t{tmp_path / 'headset scene.funscript'}",
        ])

        assert playlist_fits_sources(playlist, str(library)) is False

    def test_every_dir_of_a_multi_dir_spec_counts(self, tmp_path: Path):
        """The main player's spec is pipe-joined, and a video from any of its dirs
        is this session's own."""
        first = tmp_path / "library" / "one"
        second = tmp_path / "library" / "two"
        playlist = tmp_path / "nau_playlist.tsv"
        _write_playlist(playlist, [str(first / "scene one.mp4"), str(second / "scene two.mp4")])

        assert playlist_fits_sources(playlist, f"{first}|{second}") is True

    def test_a_sibling_dir_whose_name_merely_starts_the_same_is_outside(self, tmp_path: Path):
        """``.../library`` must not swallow ``.../library_vr`` beside it —
        matching on the raw string prefix is what would."""
        library = tmp_path / "library"
        playlist = tmp_path / "nau_playlist.tsv"
        _write_playlist(playlist, [str(tmp_path / "library_vr" / "headset scene.mp4")])

        assert playlist_fits_sources(playlist, str(library)) is False

    def test_case_alone_never_makes_a_video_foreign(self, tmp_path: Path):
        """Windows hands the same folder back in either case, and the config and
        a player's playlist need not agree — a rebuild on that would throw away
        a good resume every launch."""
        library = tmp_path / "Library" / "2D"
        playlist = tmp_path / "nau_playlist.tsv"
        _write_playlist(playlist, [str(tmp_path / "library" / "2d" / "scene one.mp4")])

        assert playlist_fits_sources(playlist, str(library)) is True

    def test_a_missing_playlist_holds_nothing_foreign(self, tmp_path: Path):
        """Having no session to come back to is the resume's own answer, and it
        must not read as a playlist needing a rebuild."""
        assert playlist_fits_sources(tmp_path / "absent.tsv", str(tmp_path)) is True


def test_resume_carries_the_satellites_mode(tmp_path):
    """A session closed in origenerator mode comes back in it, the same way the
    main slot's mode does."""
    state_file = tmp_path / "shared_state.ini"
    write_shared_state(state_file, BridgeState(satellites_mode="origenerator"))

    state = resume_shared_state(state_file, resumed=True)

    assert state.satellites_mode == "origenerator"
