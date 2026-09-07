"""The headset's cover: what it shows, and when it gets out of the way.

Everything here drives the reading end with the WRITER the orchestrator uses --
the desktop's own :class:`PhaseProgress` -- rather than hand-written file
contents.  That is the point of the branch: both apps' orchestrators speak one
channel, and a test that spelled the lines out itself would go on passing after
the two ends had drifted apart.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from fun_time.overlay_progress import (
    CANCEL_FILENAME,
    PROGRESS_FILENAME,
    SHUTDOWN_PROGRESS_FILENAME,
    SHUTDOWN_READY_FILENAME,
    PhaseProgress,
    StartupCancelled,
)
from fun_time.session_handoff import hold_the_headset, release_the_headset
from fun_time_vr.cover import (
    CANCEL_HINT,
    CANCELLING_STATUS,
    CLOSING_STATUS,
    COVER_CLEAR,
    COVER_DWELL_S,
    COVER_SIZE_PX,
    HELD_STATUS,
    SCENE_READY_FILENAME,
    SCENE_READY_GRACE_S,
    SHUTDOWN_STALE_TIMEOUT_S,
    STARTUP_STALE_TIMEOUT_S,
    VR_SHUTDOWN_PHASES,
    VR_STARTUP_PHASES,
    Cover,
    CoverAnchor,
    CoverSeen,
    CoverWatcher,
    SceneReady,
    paint_cover,
    scene_ready_file,
    wait_for_cover_painted,
)


class _Clock:
    """A hand-wound monotonic clock, so a two-minute staleness rule costs the
    suite nothing."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _startup_writer(state_dir: Path, *, cancellable: bool = True) -> PhaseProgress:
    return PhaseProgress(
        state_dir / PROGRESS_FILENAME,
        phases=VR_STARTUP_PHASES,
        cancel_file=state_dir / CANCEL_FILENAME if cancellable else None,
    )


def _shutdown_writer(state_dir: Path) -> PhaseProgress:
    return PhaseProgress(state_dir / SHUTDOWN_PROGRESS_FILENAME, phases=VR_SHUTDOWN_PHASES)


class TestThePhases:
    """A phase key the orchestrator names and this list does not carry raises
    KeyError out of ``advance`` -- which, in a VR session, happens on a headset
    with no console to say so."""

    def _advanced_keys(self, pattern: str) -> set[str]:
        source = Path("fun_time_vr/orchestrator.py").read_text(encoding="utf-8")
        return set(re.findall(pattern, source))

    def test_every_startup_phase_the_orchestrator_reports_exists(self):
        reported = self._advanced_keys(r'\bprogress\.advance\("([a-z_]+)"\)')

        assert reported == {phase.key for phase in VR_STARTUP_PHASES}

    def test_every_shutdown_phase_the_orchestrator_reports_exists(self):
        reported = self._advanced_keys(r'\bshutdown\.advance\("([a-z_]+)"\)')

        # "controls" is advanced by the context manager itself, before the body
        # the other two run in, so it is not among the body's calls.
        assert reported | {"controls"} == {phase.key for phase in VR_SHUTDOWN_PHASES}

    def test_the_last_startup_phase_is_weightless(self):
        """So the bar reads full while the last of the launch runs under the
        cover -- the desktop's final phase is weightless for the same reason."""
        assert VR_STARTUP_PHASES[-1].weight == 0.0
        assert all(phase.weight > 0 for phase in VR_STARTUP_PHASES[:-1])

    def test_the_player_is_the_last_thing_teardown_reports(self):
        """It is the thing wearing the cover, so its phase has to be last or the
        bar would claim the session was further along than it is."""
        assert VR_SHUTDOWN_PHASES[-1].key == "players"

    def test_the_closing_cover_opens_on_its_first_phase_words(self):
        """Opening on anything else reads as a flicker on the first poll."""
        assert VR_SHUTDOWN_PHASES[0].message == CLOSING_STATUS


class TestWhatTheCoverShows:
    def test_no_files_means_show_the_scene(self, tmp_path: Path):
        assert CoverWatcher(tmp_path).read() is None

    def test_a_startup_phase_shows_its_words_a_bar_and_the_way_out(self, tmp_path: Path):
        _startup_writer(tmp_path).advance("players")

        cover = CoverWatcher(tmp_path).read()

        assert cover is not None
        assert cover.status == "Waiting for players..."
        assert cover.hint == CANCEL_HINT
        assert 0.0 < cover.fraction < 1.0
        assert not cover.closing

    def test_the_weightless_last_phase_fills_the_bar(self, tmp_path: Path):
        _startup_writer(tmp_path).advance("finalizing")

        cover = CoverWatcher(tmp_path).read()

        assert cover is not None
        assert cover.fraction == pytest.approx(1.0)

    def test_done_takes_the_cover_down(self, tmp_path: Path):
        progress = _startup_writer(tmp_path)
        progress.advance("players")
        watcher = CoverWatcher(tmp_path)
        assert watcher.read() is not None

        progress.finish()

        assert watcher.read() is None

    def test_a_torn_write_holds_the_last_readable_line(self, tmp_path: Path):
        """Snapping the bar back to zero mid-launch would read as the session
        starting over."""
        _startup_writer(tmp_path).advance("players")
        watcher = CoverWatcher(tmp_path)
        settled = watcher.read()

        (tmp_path / PROGRESS_FILENAME).write_text("47/", encoding="utf-8")

        assert watcher.read() == settled

    def test_the_cancel_flag_replaces_the_status_and_takes_the_hint_away(
            self, tmp_path: Path):
        _startup_writer(tmp_path).advance("players")
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

        cover = CoverWatcher(tmp_path).read()

        assert cover is not None
        assert cover.status == CANCELLING_STATUS
        assert cover.hint == ""

    def test_a_phase_still_in_flight_cannot_flip_the_words_back(self, tmp_path: Path):
        """The teardown a cancel starts writes no phases of its own, but one
        already on its way must not put "Waiting for players..." back over a
        launch that is being taken apart."""
        progress = _startup_writer(tmp_path)
        progress.advance("companions")
        (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")
        watcher = CoverWatcher(tmp_path)
        assert watcher.read().status == CANCELLING_STATUS

        (tmp_path / PROGRESS_FILENAME).write_text("70/1030|Waiting for players...",
                                                  encoding="utf-8")

        assert watcher.read().status == CANCELLING_STATUS

    def test_the_cancelling_words_survive_the_flag_being_cleared(self, tmp_path: Path):
        """The orchestrator drops the flag at the END of the teardown it starts;
        the cover must not go back to offering a way out at that moment."""
        _startup_writer(tmp_path).advance("players")
        cancel_file = tmp_path / CANCEL_FILENAME
        cancel_file.write_text("cancel\n", encoding="utf-8")
        watcher = CoverWatcher(tmp_path)
        assert watcher.read().status == CANCELLING_STATUS

        cancel_file.unlink()

        assert watcher.read().status == CANCELLING_STATUS

    def test_a_teardown_outranks_a_startup_file_left_lying_around(self, tmp_path: Path):
        _startup_writer(tmp_path).advance("players")
        _shutdown_writer(tmp_path).advance("companions")

        cover = CoverWatcher(tmp_path).read()

        assert cover is not None
        assert cover.closing
        assert cover.status == "Closing companions..."

    def test_the_player_can_raise_the_closing_cover_with_no_file_at_all(
            self, tmp_path: Path):
        """Its own window closed, or an interrupt: nobody is going to write a
        shutdown file, and its units are about to go down one at a time."""
        watcher = CoverWatcher(tmp_path)
        assert watcher.read() is None

        watcher.closing_now()

        cover = watcher.read()
        assert cover is not None
        assert cover.closing
        assert cover.status == CLOSING_STATUS


class TestTheHeldCover:
    """A hold outlives the session that asked for it, so nothing is writing its
    progress file -- and the staleness rule that protects every other cover
    would take this one down mid-crossing (docs/entering-vr.md)."""

    def test_a_hold_puts_the_crossing_panel_up_with_no_progress_file_at_all(
        self, tmp_path: Path,
    ):
        hold_the_headset(tmp_path, stop_runtime=False)

        cover = CoverWatcher(tmp_path).read()

        assert cover is not None
        assert cover.status == HELD_STATUS
        assert cover.closing is True

    def test_it_outranks_whatever_the_teardown_file_still_says(self, tmp_path: Path):
        """The teardown that raised it has ended; its last phase would otherwise
        go on reading "Closing players..." for the whole crossing."""
        watcher = CoverWatcher(tmp_path)
        PhaseProgress(tmp_path / SHUTDOWN_PROGRESS_FILENAME,
                      phases=VR_SHUTDOWN_PHASES).advance("players")
        assert watcher.read().status != HELD_STATUS

        hold_the_headset(tmp_path, stop_runtime=False)

        assert watcher.read().status == HELD_STATUS

    def test_releasing_it_hands_the_view_back(self, tmp_path: Path):
        hold_the_headset(tmp_path, stop_runtime=False)
        watcher = CoverWatcher(tmp_path)
        assert watcher.read() is not None

        release_the_headset(tmp_path)

        assert watcher.read() is None


class TestGivingUp:
    def test_a_startup_file_that_stops_moving_gives_the_headset_back(
            self, tmp_path: Path):
        """The orchestrator died holding the cover up.  A headset must never be
        left under a panel that will never move."""
        clock = _Clock()
        _startup_writer(tmp_path).advance("players")
        watcher = CoverWatcher(tmp_path, clock=clock)
        assert watcher.read() is not None

        clock.now += STARTUP_STALE_TIMEOUT_S + 1

        assert watcher.read() is None

    def test_the_clock_restarts_on_every_write(self, tmp_path: Path):
        clock = _Clock()
        progress = _startup_writer(tmp_path)
        progress.advance("services")
        watcher = CoverWatcher(tmp_path, clock=clock)
        watcher.read()

        clock.now += STARTUP_STALE_TIMEOUT_S - 1
        progress.advance("players")
        watcher.read()
        clock.now += STARTUP_STALE_TIMEOUT_S - 1

        assert watcher.read() is not None

    def test_two_steps_inside_one_filesystem_tick_still_restart_it(
            self, tmp_path: Path):
        """A runner fast enough to write both steps within one timestamp tick
        gives them the same mtime, and a clock keyed on mtime never restarted --
        so the cover came down mid-launch, on a session that was moving fine."""
        clock = _Clock()
        progress = _startup_writer(tmp_path)
        progress.advance("services")
        watcher = CoverWatcher(tmp_path, clock=clock)
        watcher.read()
        stamped = (tmp_path / PROGRESS_FILENAME).stat()

        clock.now += STARTUP_STALE_TIMEOUT_S - 1
        progress.advance("players")
        os.utime(tmp_path / PROGRESS_FILENAME, (stamped.st_atime, stamped.st_mtime))
        watcher.read()
        clock.now += STARTUP_STALE_TIMEOUT_S - 1

        assert watcher.read() is not None

    def test_teardowns_clock_is_the_short_one(self, tmp_path: Path):
        """Nothing is left under a closing cover to wait for, so it gives up far
        sooner than a launch's does."""
        clock = _Clock()
        _shutdown_writer(tmp_path).advance("controls")
        watcher = CoverWatcher(tmp_path, clock=clock)
        assert watcher.read() is not None

        clock.now += SHUTDOWN_STALE_TIMEOUT_S + 1

        assert watcher.read() is None

    def test_an_end_given_up_on_stays_given_up(self, tmp_path: Path):
        """As the desktop's does: its cover is a destroyed window by then, and a
        room half revealed cannot be covered back up."""
        clock = _Clock()
        progress = _startup_writer(tmp_path)
        progress.advance("players")
        watcher = CoverWatcher(tmp_path, clock=clock)
        watcher.read()  # the clock starts when the cover first sees the file
        clock.now += STARTUP_STALE_TIMEOUT_S + 1
        assert watcher.read() is None

        progress.advance("finalizing")

        assert watcher.read() is None


class TestTheReadyFlag:
    def test_it_is_the_one_the_desktops_closing_screen_drops(self, tmp_path: Path):
        """Derived the desktop's way from the shutdown progress file, so the two
        ends agree on it without passing it around."""
        assert CoverWatcher(tmp_path).ready_file == tmp_path / SHUTDOWN_READY_FILENAME

    def test_the_flag_landing_is_what_teardown_was_waiting_for(self, tmp_path: Path):
        ready = tmp_path / SHUTDOWN_READY_FILENAME
        ready.write_text("", encoding="utf-8")

        assert wait_for_cover_painted(
            ready, still_alive=lambda: True, timeout_s=5.0, sleep=lambda _s: None)

    def test_a_player_that_is_already_gone_is_not_waited_for(self, tmp_path: Path):
        """No flag is ever coming, and every second here is a second the quit
        the user asked for has not happened."""
        assert not wait_for_cover_painted(
            tmp_path / SHUTDOWN_READY_FILENAME,
            still_alive=lambda: False, timeout_s=5.0, sleep=lambda _s: None,
        )

    def test_a_silent_player_is_given_up_on_at_the_deadline(self, tmp_path: Path):
        clock = _Clock()

        def sleep(seconds: float) -> None:
            clock.now += seconds

        assert not wait_for_cover_painted(
            tmp_path / SHUTDOWN_READY_FILENAME,
            still_alive=lambda: True, timeout_s=1.0, clock=clock, sleep=sleep,
        )


class TestPainting:
    @pytest.mark.parametrize("cover", [
        Cover(status="Waiting for players...", fraction=0.4, hint=CANCEL_HINT),
        Cover(status=CANCELLING_STATUS, fraction=0.4),
        Cover(status="Closing players...", fraction=1.0, closing=True),
        Cover(status="", fraction=0.0),
    ])
    def test_every_cover_paints_at_the_one_size(self, cover: Cover):
        """The screen carrying it has a fixed angular width, so a bitmap that
        changed size between repaints would rescale the whole cover."""
        image = paint_cover(cover)

        assert image.size == COVER_SIZE_PX
        assert image.mode == "RGBA"

    def test_the_bar_is_clamped_to_the_panel(self):
        """A total the writer has not caught up with must not draw off the
        edge, and a negative one must not draw backwards."""
        for fraction in (-1.0, 2.0):
            assert paint_cover(Cover(status="x", fraction=fraction)).size == COVER_SIZE_PX

    def test_the_ground_is_the_panels_own_tone(self):
        """The eye is cleared to this before the panel is drawn, so a mismatch
        would ring the panel with a rectangle of a different color."""
        image = paint_cover(Cover(status="x", fraction=0.0))

        assert tuple(round(c * 255) for c in COVER_CLEAR[:3]) == image.getpixel((0, 0))[:3]


def test_a_checkpoint_still_raises_for_the_vr_phases(tmp_path: Path):
    """The cancel the cover offers rests on this: the same reporter, the same
    flag, raising the same exception the desktop's launch unwinds on."""
    progress = _startup_writer(tmp_path)
    (tmp_path / CANCEL_FILENAME).write_text("cancel\n", encoding="utf-8")

    with pytest.raises(StartupCancelled):
        progress.advance("players")


class TestTheRoomComingUp:
    """The signal the orchestrator holds the cover for.  Without it the reveal
    happens on the player's first STATUS write -- a role having picked a video,
    not a frame of one having reached the headset -- and the four pictures land
    in the open, one at a time, which is the whole thing the cover is for."""

    def test_nothing_is_said_while_the_room_is_still_blank(self, tmp_path: Path):
        marker = tmp_path / SCENE_READY_FILENAME
        ready = SceneReady(marker, clock=_Clock())

        for _frame in range(10):
            ready.note(False)

        assert not marker.exists()

    def test_the_first_full_frame_says_so(self, tmp_path: Path):
        marker = tmp_path / SCENE_READY_FILENAME
        ready = SceneReady(marker, clock=_Clock())
        ready.note(False)

        ready.note(True)

        assert marker.exists()

    def test_it_is_said_once_and_not_again(self, tmp_path: Path):
        """It rides the frame loop, so past the first yes it costs a boolean."""
        marker = tmp_path / SCENE_READY_FILENAME
        ready = SceneReady(marker, clock=_Clock())
        ready.note(True)
        marker.unlink()

        ready.note(True)

        assert not marker.exists()

    def test_a_screen_that_never_fills_does_not_hold_the_reveal_forever(
            self, tmp_path: Path):
        """A satellite with an empty playlist never gets a texture at all: the
        reveal may be late, but it must never be absent."""
        clock = _Clock()
        marker = tmp_path / SCENE_READY_FILENAME
        ready = SceneReady(marker, grace_s=SCENE_READY_GRACE_S, clock=clock)
        ready.note(False)

        clock.now += SCENE_READY_GRACE_S + 1
        ready.note(False)

        assert marker.exists()

    def test_both_ends_derive_the_marker_from_the_state_dir(self, tmp_path: Path):
        assert scene_ready_file(tmp_path) == tmp_path / SCENE_READY_FILENAME


class TestWhereTheCoverHangs:
    """Drawn head-locked it turns with the eyes and reads as glued to the
    lenses; held to one heading it is a panel out in the world, which can be
    looked at and looked away from."""

    def test_the_first_heading_is_the_one_it_keeps(self):
        anchor = CoverAnchor()

        assert anchor.heading(1.2) == pytest.approx(1.2)
        assert anchor.heading(2.9) == pytest.approx(1.2)
        assert anchor.heading(-0.4) == pytest.approx(1.2)

    def test_a_released_anchor_takes_the_next_viewer_where_they_are(self):
        """The closing cover must not hang where the loading one did, hours and
        a headset-turn earlier."""
        anchor = CoverAnchor()
        anchor.heading(1.2)

        anchor.release()

        assert anchor.heading(2.9) == pytest.approx(2.9)

    def test_a_heading_of_zero_is_a_heading(self):
        """Held as None-or-not rather than as a truthiness, or facing the
        reference space's forward would re-anchor every frame."""
        anchor = CoverAnchor()
        anchor.heading(0.0)

        assert anchor.heading(2.9) == pytest.approx(0.0)


class TestBeingSeen:
    """A launch is over in six seconds and the headset is still on the desk;
    the first two verifications of the cover saw nothing at all because of it.
    The reveal now waits for the panel to have been in front of a WORN headset,
    so what a viewer sees on putting it on is the loading screen."""

    def test_frames_that_reached_nobody_do_not_count(self):
        clock = _Clock()
        seen = CoverSeen(clock=clock)

        for _frame in range(500):
            seen.note(False)
            clock.now += 0.1

        assert not seen.dwelt

    def test_the_dwell_runs_from_the_first_frame_that_landed(self):
        clock = _Clock()
        seen = CoverSeen(clock=clock)
        clock.now += 60  # a minute on the desk

        seen.note(True)
        assert not seen.dwelt

        clock.now += COVER_DWELL_S
        assert seen.dwelt

    def test_a_headset_taken_off_mid_dwell_does_not_restart_it(self):
        """Its wearer looked; asking them to look again from zero would hold a
        launch on a glance."""
        clock = _Clock()
        seen = CoverSeen(clock=clock)
        seen.note(True)

        clock.now += COVER_DWELL_S
        seen.note(False)

        assert seen.dwelt
