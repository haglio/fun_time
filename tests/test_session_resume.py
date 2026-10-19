"""Bringing a reopened session back to the clip each Funestra was on."""
from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

from funestra import funestra_verbs
from funestra.console import OSR2_PARKED, OSR2_RETRACTED
from funestra.genau_controls import VERBS as GENAU_VERBS
from funestra.modes import MainMode, SatellitesMode

from fun_time.bridge_records import SatelliteChannel
from fun_time.crown import Crown
from fun_time.funestra_handover import hand_back, keep_aside
from fun_time.funestra_settings import FunestraReports
from fun_time.funestra_status import MainFunestraStatus
from fun_time.main_list import MainList
from fun_time.origenerator_switches import RfbSlot
from fun_time.room_funestras import RoomFunestra
from fun_time.session_resume import (
    DESCRIBES_THE_PLAYLIST,
    NOT_RESUMED,
    NOT_RESUMED_PER_SATELLITE,
    RESUMED_FIELDS,
    RESUMED_SATELLITE_FIELDS,
    open_the_main_list,
    playlist_opens_on,
    resume_genau,
    resume_main_lock,
    resume_main_loop,
    resume_playlists,
    resume_rates,
    resume_satellite_locks,
    resume_shared_state,
    resume_what_lives_in_a_funestra,
)
from fun_time.shared_state import BridgeState, SatelliteState, read_shared_state, write_shared_state
from tests.made_up_state import remembered_values, shifted_state


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


def _never_came_home(
    tmp_path: Path, *, own: list[str], showing: str, hosted: list[str],
) -> SatelliteChannel:
    """The Portrait Funestra as a session quit in origenerator mode leaves it: its
    own list kept aside the way entering that mode keeps it, the hosted app's
    list in its playlist file."""
    channel = SatelliteChannel(
        cmd_file=tmp_path / "portrait_cmd.txt",
        paused_file=tmp_path / "portrait_paused.txt",
        status_file=tmp_path / "portrait_status.txt",
        playlist_file=tmp_path / "portrait_playlist.tsv",
        sources="",
    )
    _write_playlist(channel.playlist_file, own)
    channel.status_file.write_text(f"video={showing}\n", encoding="utf-8")
    keep_aside(channel)
    _write_playlist(channel.playlist_file, hosted)
    return channel


class TestResumePlaylists:
    def test_rotates_a_playlist_onto_the_clip_that_was_on_screen(self, tmp_path: Path):
        """A Funestra starts at the top of its playlist file, so putting last
        session's clip there is what makes it resume — and rotating (rather than
        moving it to the front) keeps the clips that were coming up coming up."""
        a, b, c = _clips(tmp_path, "a.mp4", "b.mp4", "c.mp4")
        playlist = tmp_path / "portrait_playlist.tsv"
        _write_playlist(playlist, [a, b, c])

        assert resume_playlists([(playlist, b)]) is True

        assert playlist.read_text(encoding="utf-8").splitlines() == [b, c, a]

    def test_carries_the_funscript_column_through_the_rotation(self, tmp_path: Path):
        """A playlist pairs each video with the funscript that drives the OSR2
        through it; rewriting the file without that column would leave a resumed
        session silently unscripted."""
        a, b, c = _clips(tmp_path, "a.mp4", "b.mp4", "c.mp4")
        playlist = tmp_path / "portrait_playlist.tsv"
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
        """One build writes both satellites' playlists, so they live or die
        together: if either has no session to come back to, the caller rebuilds
        the pair and each file must still be as the build left it."""
        a, b = _clips(tmp_path, "a.mp4", "b.mp4")
        portrait = tmp_path / "portrait_playlist.tsv"
        _write_playlist(portrait, [a, b])

        resumed = resume_playlists([
            (portrait, b),
            (tmp_path / "landscape_playlist.tsv", ""),
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

    def test_opens_a_funestra_that_never_came_home_on_its_own_list(self, tmp_path: Path):
        """A session quit while the hosted Origenerator had the Funestra leaves that
        app's pictures in the playlist file and the Funestra's own list kept aside.
        Every room is built in kino mode, so its own list is what it opens on —
        at the clip it was showing when it left."""
        a, b, picture = _clips(tmp_path, "a.mp4", "b.mp4", "picture.png")
        channel = _never_came_home(tmp_path, own=[a, b], showing=b, hosted=[picture])

        assert resume_playlists([(channel.playlist_file, picture)]) is True

        assert channel.playlist_file.read_text(encoding="utf-8").splitlines() == [b, a]

    def test_spends_a_kept_list_even_when_there_is_nothing_to_resume(self, tmp_path: Path):
        """The caller then rebuilds every playlist, and a kept list that outlived
        the rebuild would be dealt over it the next time that Funestra came home."""
        a, picture = _clips(tmp_path, "a.mp4", "picture.png")
        channel = _never_came_home(tmp_path, own=[a], showing=a, hosted=[picture])

        resumed = resume_playlists([
            (tmp_path / "landscape_playlist.tsv", ""),
            (channel.playlist_file, picture),
        ])

        assert resumed is False
        assert hand_back(channel) is False


class TestResumeSharedState:
    """The other half of a resume: coming back in the mode you left in.

    The playlist files carry F-mode, each side's filter and order, and any group
    loop baked into them, so a session that resumed those files and opened on a
    default state described itself wrongly on every HUD — and answered the next
    "F-mode" by reporting it *enabled* while nothing visibly changed.
    """

    def test_a_room_saved_before_it_kept_its_funestras_settings_takes_their_last_reports(
            self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        state_file.write_text("[state]\nvolume = 40\n", encoding="utf-8")

        state = resume_shared_state(state_file, resumed=True, last_reported=FunestraReports(
            main=MainFunestraStatus(speed=1.5, read_at=1_700_000_000.0)))

        assert (state.volume, state.main_speed) == (40, 1.5)

    def test_a_room_saved_before_it_kept_the_mic_takes_the_flag_it_left_and_spends_it(
            self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        state_file.write_text("[state]\nvolume = 40\n", encoding="utf-8")
        (tmp_path / "mic_off.flag").write_text("1", encoding="utf-8")

        state = resume_shared_state(state_file, resumed=True)

        assert state.mic_muted is True
        assert not (tmp_path / "mic_off.flag").exists()

    def test_what_a_funestra_reported_after_the_room_last_wrote_its_state_comes_back(
            self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(main_speed=0.5))

        state = resume_shared_state(state_file, resumed=True, last_reported=FunestraReports(
            main=MainFunestraStatus(speed=1.5, read_at=1_700_000_000.0)))

        assert state.main_speed == 1.5

    def test_every_setting_it_remembers_comes_back(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        left = replace(shifted_state(BridgeState()), osr2_control=OSR2_RETRACTED)
        write_shared_state(state_file, left)

        state = resume_shared_state(state_file, resumed=True)

        assert remembered_values(state) == remembered_values(left)

    def test_carries_each_funestras_own_f_mode_onto_the_resumed_session(self, tmp_path: Path):
        """A resumed playlist holds whatever its own Funestra was narrowed to, so
        each F-mode comes back as it was closed — and each HUD has to say so.
        Carrying one flag for all three would put Funestras into an F-mode whose
        playlist was never built under it."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(landscape=SatelliteState(favorites_filter=True), portrait=SatelliteState(favorites_filter=False), main_scripted_filter=True))

        state = resume_shared_state(state_file, resumed=True)

        assert (state.main_scripted_filter, state.satellite(RoomFunestra.PORTRAIT).favorites_filter,
                state.satellite(RoomFunestra.LANDSCAPE).favorites_filter) == (True, False, True)

    def test_carries_a_running_loop_and_the_map_it_hangs_on(self, tmp_path: Path):
        """A loop IS the group written out as the side's playlist, so resuming
        that file resumes the loop — the HUD has to keep its button lit and its
        map frozen where the loop left it, not read the queue as a browse."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(landscape=SatelliteState(loop="action", map_anchor="C:/v/b.mp4"), portrait=SatelliteState(loop="seed", map_anchor="C:/v/a.mp4", widen_clip="C:/v/a.mp4")))

        state = resume_shared_state(state_file, resumed=True)

        assert state.satellite(RoomFunestra.PORTRAIT).loop == "seed"
        assert state.satellite(RoomFunestra.PORTRAIT).map_anchor == "C:/v/a.mp4"
        assert state.satellite(RoomFunestra.PORTRAIT).widen_clip == "C:/v/a.mp4"
        assert state.satellite(RoomFunestra.LANDSCAPE).loop == "action"
        assert state.satellite(RoomFunestra.LANDSCAPE).map_anchor == "C:/v/b.mp4"

    def test_carries_each_side_s_filter_and_order(self, tmp_path: Path):
        """Both narrowed the playlist that just came back, so both are still in
        force and belong on the status line."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(landscape=SatelliteState(filter="beta gamma", latest=True), portrait=SatelliteState(filter="alpha", latest=True)))

        state = resume_shared_state(state_file, resumed=True)

        assert (state.satellite(RoomFunestra.PORTRAIT).filter, state.satellite(RoomFunestra.LANDSCAPE).filter) == ("alpha", "beta gamma")
        assert state.satellite(RoomFunestra.PORTRAIT).latest is True
        assert state.satellite(RoomFunestra.LANDSCAPE).latest is True

    def test_carries_the_main_funestra_s_order(self, tmp_path: Path):
        """The Main Funestra's order fixes its resumed playlist exactly as a
        satellite's does, so it comes back with it — otherwise the session opens
        on a newest-first list while its HUD says Shuffle, and the next rebuild
        reshuffles the order it was left in."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(main_latest=True))

        state = resume_shared_state(state_file, resumed=True)

        assert state.main_latest is True

    def test_carries_genau_s_order(self, tmp_path: Path):
        """Genau rescans its folder every launch, so its order has no file to
        ride back in on; it is named on the launch instead, off this flag.
        Dropped, the session opened shuffled under a console that also said so,
        having been left browsing Latest."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(genau_latest=True))

        state = resume_shared_state(state_file, resumed=True)

        assert state.genau_latest is True

    def test_carries_the_sound_level_and_each_side_s_lock(self, tmp_path: Path):
        """Neither is a thing that should reset overnight, so both come back —
        and each has its world put back with it, the level seeded to both audio
        sinks at startup and the lock re-sent to its satellite."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(landscape=SatelliteState(locked=False), portrait=SatelliteState(locked=True), volume=40, muted=True))

        state = resume_shared_state(state_file, resumed=True)

        assert (state.volume, state.muted) == (40, True)
        assert (state.satellite(RoomFunestra.PORTRAIT).locked, state.satellite(RoomFunestra.LANDSCAPE).locked) == (True, False)

    def test_carries_what_the_osr2_was_left_in(self, tmp_path: Path):
        """Held at one end, it stays that way: the device arbiter carries the
        state out on the new session's first tick, the same as it does for a
        press."""
        state_file = tmp_path / "shared_bridge_state.ini"
        for control in (OSR2_PARKED, OSR2_RETRACTED):
            write_shared_state(state_file, BridgeState(osr2_control=control))

            assert resume_shared_state(state_file, resumed=True).osr2_control == control

    def test_a_session_left_under_control_off_comes_back_parked(self, tmp_path: Path):
        """Control off went when the four buttons became one switch; the
        switch's off is parked, which is also where control off had left the
        device."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(osr2_control="control_off"))

        assert resume_shared_state(state_file, resumed=True).osr2_control == OSR2_PARKED

    def test_carries_which_funestra_has_the_osr2(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(osr2_funestra=RoomFunestra.LANDSCAPE))

        assert resume_shared_state(state_file, resumed=True).osr2_funestra == RoomFunestra.LANDSCAPE

    def test_carries_the_mode_the_primary_slot_was_left_in(self, tmp_path: Path):
        """Which Funestra owns the big display is as much a thing you set as the
        sound level, so leaving the session showing Genau and reopening on the Main Funestra
        is the same overnight reset.  Startup builds every session in kino mode
        and then puts the carried mode on over the top (see
        :func:`fun_time.windows_bridge_startup.seed_startup_states`)."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(main_mode=MainMode.GENAU))

        assert resume_shared_state(state_file, resumed=True).main_mode is MainMode.GENAU

    def test_drops_the_state_nothing_on_disk_brings_back(self, tmp_path: Path):
        """OmniPause's flags are cleared before the Funestras launch, and a
        keyboard selection was never a thing you could leave running.  Carrying
        either forward would be the same lie in the other direction."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(landscape=SatelliteState(nav_anchor="C:/v/b.mp4"), portrait=SatelliteState(nav_anchor="C:/v/a.mp4"), omni_paused=True, active_funestra=3))

        state = resume_shared_state(state_file, resumed=True)

        fresh = BridgeState()
        for name in NOT_RESUMED:
            assert getattr(state, name) == getattr(fresh, name)
        for funestra in RoomFunestra.SATELLITES:
            for name in NOT_RESUMED_PER_SATELLITE:
                assert getattr(state.satellite(funestra), name) == getattr(
                    fresh.satellite(funestra), name)

    def test_a_room_reopens_with_the_windows_where_startup_puts_them(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(crowned=Crown.MAIN, majority=Crown.MAIN))

        state = resume_shared_state(state_file, resumed=True)

        assert (state.crowned, state.majority) == (Crown.MAIN, Crown.PORTRAIT)

    def test_the_state_left_behind_is_named_positively_and_is_all_of_it(self):
        """The list used to run the other way: what came BACK was spelled out,
        so a field added to BridgeState and not added here was dropped, silently
        and by default.  Dropping is now the exception, and every exception is a
        field that exists.
        """
        names = {field.name for field in fields(BridgeState)}
        assert names >= NOT_RESUMED, sorted(NOT_RESUMED - names)
        assert set(RESUMED_FIELDS) == names - NOT_RESUMED

        side_names = {field.name for field in fields(SatelliteState)}
        assert side_names >= NOT_RESUMED_PER_SATELLITE, sorted(NOT_RESUMED_PER_SATELLITE - side_names)
        assert set(RESUMED_SATELLITE_FIELDS) == side_names - NOT_RESUMED_PER_SATELLITE


class TestResumeSatelliteLocks:
    """A lock is repeat-one in mpv's own loop_file, so it dies with the Funestra
    process and has to be re-sent — unlike a filter or a loop, which ride back in
    on the playlist file the new Funestra reads."""

    def test_queues_a_lock_for_the_side_that_was_holding_one(self, tmp_path: Path):
        portrait = tmp_path / "portrait_cmd.txt"
        landscape = tmp_path / "landscape_cmd.txt"

        resume_satellite_locks([(portrait, True), (landscape, False)])

        assert portrait.read_text(encoding="utf-8").split() == ["LOCK_ON"]
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

        assert portrait.read_text(encoding="utf-8").split() == ["RELOAD_PLAYLIST", "LOCK_ON"]

    def test_satellites_built_fresh_start_over_and_every_other_setting_comes_back(
            self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"
        left = replace(shifted_state(BridgeState()), osr2_control=OSR2_RETRACTED)
        write_shared_state(state_file, left)

        state = resume_shared_state(state_file, resumed=False)

        started_over = left
        for funestra in RoomFunestra.SATELLITES:
            started_over = started_over.with_satellite(funestra, **{
                name: getattr(SatelliteState(), name) for name in DESCRIBES_THE_PLAYLIST})
        assert remembered_values(replace(state, main_shuffle_seed=0)) == remembered_values(
            replace(started_over, main_shuffle_seed=0))

    def test_a_fresh_session_deals_the_main_funestra_a_shuffle_of_its_own(self, tmp_path: Path):
        state_file = tmp_path / "shared_bridge_state.ini"

        seeds = {resume_shared_state(state_file, resumed=False).main_shuffle_seed
                 for _ in range(3)}

        assert len(seeds) == 3

    def test_the_seeded_state_is_what_the_session_opens_on(self, tmp_path: Path):
        """The dispatch loop reads its state off this file every tick and never
        hears about the return value, so the carry has to be on disk."""
        state_file = tmp_path / "shared_bridge_state.ini"
        write_shared_state(state_file, BridgeState(portrait=SatelliteState(favorites_filter=True), omni_paused=True))

        resume_shared_state(state_file, resumed=True)

        written = read_shared_state(state_file)
        assert written is not None
        assert written.satellite(RoomFunestra.PORTRAIT).favorites_filter is True
        assert written.omni_paused is False

    def test_a_first_run_leaves_a_state_file(self, tmp_path: Path):
        """There is nothing to read on a wiped state dir, and the session still
        has to open on a state — a written default, not a missing file."""
        state_file = tmp_path / "shared_bridge_state.ini"

        assert replace(resume_shared_state(state_file, resumed=True), main_shuffle_seed=0) == (
            BridgeState())
        assert state_file.exists()


class TestResumeMainLoop:
    """The Main Funestra's A/B loop is a range inside one video, held in a Funestra
    process that is about to be replaced — so like a satellite's lock it cannot
    ride back in on a file the new Funestra reads, and has to be re-sent.  Either
    orchestrator asks for it, so the whole decision is here rather than at each
    of them: a desktop session and a headset one part company over which clips
    they can play, not over what a loop is."""

    def _playlist(self, tmp_path: Path, *videos: str) -> Path:
        playlist = tmp_path / "main_player_playlist.tsv"
        _write_playlist(playlist, list(_clips(tmp_path, *videos)))
        return playlist

    def _looping(self, tmp_path: Path, video: str) -> BridgeState:
        return BridgeState(main_loop_video=str(tmp_path / video), main_loop_in_ms=2000,
                           main_loop_out_ms=4000)

    def test_queues_the_range_the_primary_was_looping(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"
        playlist = self._playlist(tmp_path, "a.mp4", "b.mp4")

        resume_main_loop(main_funestra_cmd, self._looping(tmp_path, "a.mp4"), playlist)

        assert main_funestra_cmd.read_text(encoding="utf-8").splitlines() == ["SET_LOOP 2000 4000"]

    def test_queues_nothing_when_there_was_no_loop(self, tmp_path: Path):
        """A Main Funestra that was not looping must be sent nothing at all: playing
        the video through is already what no loop means."""
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"
        playlist = self._playlist(tmp_path, "a.mp4")

        resume_main_loop(main_funestra_cmd, BridgeState(), playlist)

        assert not main_funestra_cmd.exists()

    def test_queues_nothing_when_another_video_leads_the_playlist(self, tmp_path: Path):
        """A rebuilt playlist, or a clip trashed since, leaves some other video at
        the top -- and these bounds would mark out a stretch of a video nobody
        chose.  This is the crossing between the two apps: each refuses the
        other's primary playlist and rebuilds its own."""
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"
        playlist = self._playlist(tmp_path, "b.mp4")

        resume_main_loop(main_funestra_cmd, self._looping(tmp_path, "a.mp4"), playlist)

        assert not main_funestra_cmd.exists()

    def test_keeps_whatever_is_already_queued(self, tmp_path: Path):
        """Startup has already seeded this channel with the sound level and the
        F-mode flag, and the Main Funestra has drained none of it yet."""
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"
        main_funestra_cmd.write_text("SET_VOLUME 40 0\n", encoding="utf-8")
        playlist = self._playlist(tmp_path, "a.mp4")

        resume_main_loop(main_funestra_cmd, self._looping(tmp_path, "a.mp4"), playlist)

        assert main_funestra_cmd.read_text(encoding="utf-8").splitlines() == [
            "SET_VOLUME 40 0", "SET_LOOP 2000 4000",
        ]

class TestResumeRates:
    def test_queues_the_rate_each_funestra_was_playing_at(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"
        portrait_cmd = tmp_path / "portrait_cmd.txt"

        resume_rates([(main_funestra_cmd, 0.25), (portrait_cmd, 1.5)])

        assert main_funestra_cmd.read_text(encoding="utf-8").splitlines() == ["SET_SPEED 0.25"]
        assert portrait_cmd.read_text(encoding="utf-8").splitlines() == ["SET_SPEED 1.5"]

    def test_queues_nothing_for_a_funestra_at_normal_speed(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"

        resume_rates([(main_funestra_cmd, 1.0)])

        assert not main_funestra_cmd.exists()


class TestResumeSatelliteVolumes:
    def test_a_satellite_gets_back_the_level_and_mute_it_was_left_at(self, tmp_path: Path):
        portrait_cmd, landscape_cmd = tmp_path / "portrait_cmd.txt", tmp_path / "landscape_cmd.txt"
        carried = BridgeState().with_satellite(RoomFunestra.PORTRAIT, volume=40, muted=False)

        resume_what_lives_in_a_funestra(
            carried, main_funestra_cmd_file=tmp_path / "main_player_cmd.txt",
            satellite_cmd_files={RoomFunestra.PORTRAIT: portrait_cmd,
                                 RoomFunestra.LANDSCAPE: landscape_cmd},
            genau_cmd_file=tmp_path / "genau_cmd.txt")

        assert portrait_cmd.read_text(encoding="utf-8").splitlines() == ["SET_VOLUME 40 0"]
        assert not landscape_cmd.exists()


class TestResumeMainLock:
    """The Main Funestra opens holding the video it loads, so unlike a satellite's
    lock it is the UNheld video that has to be re-sent."""

    def test_queues_the_unlock_a_main_funestra_was_left_in(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"

        resume_main_lock(main_funestra_cmd, locked=False)

        assert main_funestra_cmd.read_text(encoding="utf-8").splitlines() == ["LOCK_OFF"]

    def test_queues_nothing_for_a_main_funestra_that_was_holding_its_video(self, tmp_path: Path):
        main_funestra_cmd = tmp_path / "main_player_cmd.txt"

        resume_main_lock(main_funestra_cmd, locked=True)

        assert not main_funestra_cmd.exists()


class TestWhatARestartBringsBackOfEachFunestra:
    _KEPT_IN_THE_ROOMS_STATE = {
        "SET_SPEED": {"main_speed", "speed"}, "SPEED_UP": {"main_speed", "speed"},
        "SPEED_DOWN": {"main_speed", "speed"}, "LOCK_ON": {"main_locked", "locked"},
        "LOCK_OFF": {"main_locked", "locked"}, "TOGGLE_LOCK": {"main_locked", "locked"},
        "SET_MAX_INTENSITY": {"max_intensity"}, "SET_VOLUME": {"volume", "muted"},
        "SHOW": {"main_mode"},
        "SET_F_MODE": {"main_scripted_filter"}}
    _NO_SETTING = frozenset({
        "NEXT", "PREV", "QUIT", "RELOAD_PLAYLIST", "SEEK_BACK", "SEEK_FWD", "TRASH", "SHOW_FRAME",
        "CLEAR_FRAME", "SET_PACE", "PLAY_FILE", "NEXT_VERSION", "PREV_VERSION"})
    _RESENT_EVERY_SECOND_BY_THE_DEVICE_ARBITER = frozenset({"SET_TCODE_ENABLED"})

    def test_every_verb_a_funestra_answers_comes_back_after_a_restart_or_is_no_setting(self):
        named = (set(self._KEPT_IN_THE_ROOMS_STATE) | self._NO_SETTING
                 | self._RESENT_EVERY_SECOND_BY_THE_DEVICE_ARBITER)
        verbs = {name for name, spelled in vars(funestra_verbs).items() if spelled == name}

        assert verbs == named, (
            f"say how a restart brings back {sorted(verbs - named)}; "
            f"a Funestra no longer answers {sorted(named - verbs)}")

    def test_what_a_restart_resends_is_what_the_rooms_state_keeps(self, tmp_path: Path):
        main_funestra_cmd, portrait_cmd = tmp_path / "main_player_cmd.txt", tmp_path / "portrait_cmd.txt"
        carried = shifted_state(BridgeState())

        resume_what_lives_in_a_funestra(
            carried, main_funestra_cmd_file=main_funestra_cmd,
            satellite_cmd_files={RoomFunestra.PORTRAIT: portrait_cmd},
            genau_cmd_file=tmp_path / "genau_cmd.txt")
        resume_satellite_locks([(portrait_cmd, True)])

        assert {line.split()[0] for command_file in (main_funestra_cmd, portrait_cmd)
                for line in command_file.read_text(encoding="utf-8").splitlines()} == {
            funestra_verbs.SET_SPEED, funestra_verbs.LOCK_OFF, funestra_verbs.SET_VOLUME,
            funestra_verbs.LOCK_ON}
        assert {funestra_verbs.SET_SPEED, funestra_verbs.LOCK_OFF, funestra_verbs.SET_VOLUME,
                funestra_verbs.LOCK_ON} <= {
            getattr(funestra_verbs, name) for name in self._KEPT_IN_THE_ROOMS_STATE}

    def test_what_the_rooms_state_keeps_for_a_funestra_is_remembered(self):
        assert set().union(*self._KEPT_IN_THE_ROOMS_STATE.values()) <= (
            set(RESUMED_FIELDS) | set(RESUMED_SATELLITE_FIELDS))


class TestResumeGenau:
    """Genau's Amp, Speed and Center bars, its shape, its flick seconds and its two
    switches live in the engine that just died, and its own launch opens them at the
    family's defaults."""

    def test_queues_the_bars_the_hand_was_left_at(self, tmp_path: Path):
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, BridgeState(genau_speed=76, genau_amplitude=60, genau_center=30,
                                            genau_flick_seconds=25))

        assert genau_cmd.read_text(encoding="utf-8").splitlines() == [
            "SPEED 76", "AMP 60", "CENTER 30", "FLICK_SECONDS 25",
        ]

    def test_queues_nothing_for_a_hand_left_where_the_next_one_opens(self, tmp_path: Path):
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, BridgeState())

        assert not genau_cmd.exists()

    def test_says_a_shape_as_the_steps_that_reach_it(self, tmp_path: Path):
        """No verb names a shape outright, so the one to take up is said as the
        steps from the shape a fresh engine opens on."""
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, BridgeState(genau_shape="sawtooth"))

        assert genau_cmd.read_text(encoding="utf-8").splitlines() == [
            "CYCLE_SHAPE", "CYCLE_SHAPE", "CYCLE_SHAPE",
        ]

    def test_queues_the_learned_motion_that_was_running(self, tmp_path: Path):
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, BridgeState(genau_learned=True))

        assert genau_cmd.read_text(encoding="utf-8").splitlines() == ["LEARNED_ON"]

    def test_queues_the_unlock_genau_was_left_in(self, tmp_path: Path):
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, BridgeState(genau_locked=False))

        assert genau_cmd.read_text(encoding="utf-8").splitlines() == ["LOCK_OFF"]

    _KEPT_IN_THE_ROOMS_STATE = {
        "speed": "genau_speed", "amplitude": "genau_amplitude", "center": "genau_center",
        "shape": "genau_shape", "flick_seconds": "genau_flick_seconds",
        "learned": "genau_learned", "lock": "genau_locked", "max_intensity": "max_intensity",
        "volume": "volume", "browse_order": "genau_latest", "shapes": "genau_plays_vr",
        "hold": "osr2_control"}
    _NO_SETTING = frozenset({
        "quit", "flick", "condemn", "play_file", "flip_ends", "quarter_cycle", "pause", "tcode",
        "hud"})

    def test_every_control_genau_has_comes_back_after_a_restart_or_is_no_setting(self):
        named = set(self._KEPT_IN_THE_ROOMS_STATE) | self._NO_SETTING
        controls = {control.name for control, _ in GENAU_VERBS.values()}

        assert controls == named, (
            f"say how a restart brings back {sorted(controls - named)}; "
            f"Genau no longer has {sorted(named - controls)}")

    def test_a_restart_resends_what_the_rooms_state_keeps_for_genau(self, tmp_path: Path):
        genau_cmd = tmp_path / "genau_cmd.txt"

        resume_genau(genau_cmd, replace(shifted_state(BridgeState()), genau_shape="sawtooth"))

        resent = {GENAU_VERBS[line.split()[0]][0].name
                  for line in genau_cmd.read_text(encoding="utf-8").splitlines()}
        assert {self._KEPT_IN_THE_ROOMS_STATE[control] for control in resent} == {
            name for name in RESUMED_FIELDS if name.startswith("genau_")
            and name not in ("genau_latest", "genau_plays_vr", "genau_plays_flat")}

    def test_what_the_rooms_state_keeps_for_genau_is_remembered(self):
        assert set(self._KEPT_IN_THE_ROOMS_STATE.values()) <= set(RESUMED_FIELDS)


class TestOpeningTheMainList:
    """Every session builds the Main player's list from the buttons it was left
    on, whatever list the last session left behind: nothing reopens it."""

    def _library(self, tmp_path: Path) -> tuple[MainList, list[str], list[str]]:
        vr_dir, flat_dir = tmp_path / "library" / "VR", tmp_path / "library" / "2D"
        for folder in (vr_dir, flat_dir):
            folder.mkdir(parents=True)
        vr = _clips(vr_dir, "scene one.mp4", "scene two.mp4")
        flat = _clips(flat_dir, "scene three.mp4", "scene four.mp4")
        the_list = MainList(sources=f"{vr_dir}|{flat_dir}", state_dir=tmp_path / "state",
                            vr_dirs=str(vr_dir))
        return the_list, vr, flat

    def test_vr_only_opens_on_vr_videos_whatever_the_last_list_held(self, tmp_path: Path):
        the_list, vr, flat = self._library(tmp_path)
        _write_playlist(the_list.file, [*flat, *vr])

        open_the_main_list(the_list, BridgeState(main_plays_flat=False),
                           state_file=tmp_path / "state" / "shared_bridge_state.ini",
                           on_screen=flat[0])

        assert sorted(the_list.file.read_text(encoding="utf-8").splitlines()) == sorted(vr)

    def test_a_desktop_session_opens_on_its_own_library_whatever_the_headset_left(
        self, tmp_path: Path,
    ):
        """Its VR masters must never reach the primary monitor, and the VR video
        the headset was showing opens nothing: the list starts at its own top."""
        _headset_list, vr, flat = self._library(tmp_path)
        desktop = MainList(sources=str(Path(flat[0]).parent), state_dir=tmp_path / "state")
        _write_playlist(desktop.file, [*vr, *flat])

        open_the_main_list(desktop, BridgeState(),
                           state_file=tmp_path / "state" / "shared_bridge_state.ini",
                           on_screen=vr[0])

        assert sorted(desktop.file.read_text(encoding="utf-8").splitlines()) == sorted(flat)

    def test_it_opens_on_the_video_it_was_left_on_so_its_loop_can_come_back(self, tmp_path: Path):
        the_list, vr, _flat = self._library(tmp_path)

        open_the_main_list(the_list, BridgeState(),
                           state_file=tmp_path / "state" / "shared_bridge_state.ini",
                           on_screen=vr[1])

        assert playlist_opens_on(the_list.file, vr[1]) is True

    def test_with_nothing_let_through_and_nothing_to_hold_its_buttons_go_back_to_their_defaults(
        self, tmp_path: Path,
    ):
        the_list, vr, flat = self._library(tmp_path)
        state_file = tmp_path / "state" / "shared_bridge_state.ini"
        left = BridgeState(main_plays_vr=False, main_plays_flat=False, main_latest=True,
                           main_shuffle_seed=7, volume=40)

        opened = open_the_main_list(the_list, left, state_file=state_file,
                                    on_screen=str(tmp_path / "library" / "gone since.mp4"))

        assert opened == replace(left, main_plays_vr=True, main_plays_flat=True, main_latest=False)
        assert read_shared_state(state_file) == opened
        assert sorted(the_list.file.read_text(encoding="utf-8").splitlines()) == sorted(vr + flat)


class TestPlaylistOpensOn:
    """Whether the clip a Funestra will actually load is the one expected — asked
    of the Main Funestra before its loop is handed back, since a loop belongs to one
    video and would otherwise be applied to whatever took its place."""

    def test_the_clip_a_resumed_playlist_leads_with(self, tmp_path: Path):
        a, b = _clips(tmp_path, "a.mp4", "b.mp4")
        playlist = tmp_path / "main_player_playlist.tsv"
        _write_playlist(playlist, [b, a])

        assert playlist_opens_on(playlist, b) is True
        assert playlist_opens_on(playlist, a) is False

    def test_case_alone_is_not_a_different_clip(self, tmp_path: Path):
        """The playlist and the status file are written by different processes,
        and Windows hands the same file back in either case."""
        playlist = tmp_path / "main_player_playlist.tsv"
        _write_playlist(playlist, [str(tmp_path / "Sub" / "a.mp4")])

        assert playlist_opens_on(playlist, str(tmp_path / "sub" / "A.MP4")) is True

    def test_a_funscript_column_does_not_hide_the_video(self, tmp_path: Path):
        a, b = _clips(tmp_path, "a.mp4", "b.mp4")
        playlist = tmp_path / "main_player_playlist.tsv"
        _write_playlist(playlist, [f"{a}\ta.funscript", b])

        assert playlist_opens_on(playlist, a) is True

    def test_an_empty_playlist_opens_on_nothing(self, tmp_path: Path):
        assert playlist_opens_on(tmp_path / "absent.tsv", "C:/v/a.mp4") is False


def test_a_room_comes_back_with_origenerator_on_what_it_held_when_left(tmp_path):
    """A satellite left in its Origenerator mode opens in it, and so does
    Origenerator left in the Random Favs Browser's place.

    The room does not wait out the hosted app's boot and nothing rearranges
    itself later either: the mode is what the room is in from its first tick,
    and the app arrives into a mode already set rather than switching the sides
    under whatever was on them.
    """
    state_file = tmp_path / "shared_state.ini"
    write_shared_state(state_file, BridgeState(
        landscape=SatelliteState(mode=SatellitesMode.ORIGENERATOR),
        rfb_slot=RfbSlot.ORIGENERATOR))

    resumed = resume_shared_state(state_file, resumed=True)

    assert resumed.satellite(RoomFunestra.LANDSCAPE).mode is SatellitesMode.ORIGENERATOR
    assert resumed.satellite(RoomFunestra.PORTRAIT).mode is SatellitesMode.KINO
    assert resumed.rfb_slot is RfbSlot.ORIGENERATOR
