from __future__ import annotations

from pathlib import Path

from fun_time.dashboard_bridge import write_dashboard_snapshot
from fun_time.dashboard_runtime import load_dashboard_snapshot
from fun_time.player_status import (
    GenauStatus,
    NauStatus,
    is_broker_heartbeat_fresh,
    is_osr2_device_on,
    read_genau_status,
    read_nau_status,
)


def test_load_dashboard_snapshot_returns_none_when_missing(tmp_path: Path):
    assert load_dashboard_snapshot(tmp_path / "missing.ini") is None


def test_load_dashboard_snapshot_reads_a_window_section_no_writer_emits(tmp_path: Path):
    # Deliberately hand-rolled: the reader still restores a persisted geometry
    # from a [window] section, and nothing in the family writes one — see the
    # note in CHANGELOG.md.  Sections it no longer parses are here too, to pin
    # that an older, richer export still loads rather than raising.
    snapshot_file = tmp_path / "dashboard_state.ini"
    snapshot_file.write_text(
        "\n".join(
            [
                "[osr2]",
                "mode=auto",
                "[main]",
                "mode=nau",
                "path=demo-main.mp4",
                "locked=0",
                "[window]",
                "x=100",
                "y=200",
                "width=300",
                "height=400",
            ]
        ),
        encoding="utf-8",
    )

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.window.x == 100
    assert snapshot.window.y == 200
    assert snapshot.window.width == 300
    assert snapshot.window.height == 400


def test_the_writers_own_export_reads_back_with_a_zero_window(tmp_path: Path):
    """The writer emits no [window] section; the reader answers zeros for it,
    not a crash — which is what leaves the geometry restore unreachable."""
    snapshot_file = tmp_path / "dashboard_state.ini"
    write_dashboard_snapshot(snapshot_file)

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.window.width == 0


def test_load_dashboard_snapshot_reads_omnipause_state(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    write_dashboard_snapshot(snapshot_file, omni_paused=True)

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.omni_paused is True


def test_load_dashboard_snapshot_defaults_omnipause_to_false(tmp_path: Path):
    # Hand-rolled on purpose: the section must be ABSENT, and the writer
    # always emits it — this pins the reader against the older export.
    snapshot_file = tmp_path / "dashboard_state.ini"
    snapshot_file.write_text("[voice]\nactive=1\n", encoding="utf-8")

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.omni_paused is False


def test_load_dashboard_snapshot_reads_voice_active(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    write_dashboard_snapshot(snapshot_file, voice_active=False)

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.voice_active is False


def test_load_dashboard_snapshot_defaults_voice_active_to_true(tmp_path: Path):
    # Hand-rolled on purpose, like the omnipause default above: the [voice]
    # section must be absent, which the writer never produces.
    snapshot_file = tmp_path / "dashboard_state.ini"
    snapshot_file.write_text("[omnipause]\nactive=0\n", encoding="utf-8")

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.voice_active is True


def test_osr2_device_on_when_rx_recent(tmp_path: Path):
    rx_file = tmp_path / "osr2_serial_rx.txt"
    rx_file.write_text("100.0", encoding="utf-8")

    assert is_osr2_device_on(rx_file, now=115.0) is True


def test_osr2_device_off_when_rx_stale(tmp_path: Path):
    rx_file = tmp_path / "osr2_serial_rx.txt"
    rx_file.write_text("100.0", encoding="utf-8")

    assert is_osr2_device_on(rx_file, now=117.0) is False


def test_osr2_device_off_when_rx_missing(tmp_path: Path):
    assert is_osr2_device_on(tmp_path / "missing.txt", now=100.0) is False


def test_osr2_device_off_when_rx_invalid(tmp_path: Path):
    rx_file = tmp_path / "osr2_serial_rx.txt"
    rx_file.write_text("not-a-float", encoding="utf-8")

    assert is_osr2_device_on(rx_file, now=100.0) is False


def test_broker_heartbeat_is_fresh_when_recent(tmp_path: Path):
    heartbeat_file = tmp_path / "broker_heartbeat.txt"
    heartbeat_file.write_text("100.0", encoding="utf-8")

    assert is_broker_heartbeat_fresh(heartbeat_file, max_age_seconds=3.0, now=102.5) is True


def test_broker_heartbeat_is_stale_when_old_or_invalid(tmp_path: Path):
    stale_file = tmp_path / "stale_heartbeat.txt"
    stale_file.write_text("100.0", encoding="utf-8")
    invalid_file = tmp_path / "invalid_heartbeat.txt"
    invalid_file.write_text("not-a-float", encoding="utf-8")

    assert is_broker_heartbeat_fresh(stale_file, max_age_seconds=3.0, now=104.0) is False
    assert is_broker_heartbeat_fresh(invalid_file, now=101.0) is False
    assert is_broker_heartbeat_fresh(tmp_path / "missing.txt", now=101.0) is False


def test_read_genau_status_returns_defaults_when_missing(tmp_path: Path):
    status = read_genau_status(tmp_path / "missing.txt")

    assert status == GenauStatus()
    assert status.cruise_active is False
    assert status.shape == "sine"


def test_read_genau_status_parses_active_cruise_and_shape(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("cruise=1\nshape=triangle\n", encoding="utf-8")

    status = read_genau_status(status_file)

    assert status.cruise_active is True
    assert status.shape == "triangle"


def test_read_genau_status_parses_the_clip_lock(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("locked=0\n", encoding="utf-8")

    assert read_genau_status(status_file).locked is False


def test_read_genau_status_defaults_the_clip_lock_to_on(tmp_path: Path):
    """A clip repeating is where Genau opens, so a status that says nothing about
    the lock — or none at all — must not light the console's padlock the wrong
    way."""
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("cruise=0\n", encoding="utf-8")

    assert read_genau_status(status_file).locked is True
    assert read_genau_status(tmp_path / "missing.txt").locked is True


def test_read_genau_status_handles_inactive_cruise(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("cruise=0\nshape=sawtooth\n", encoding="utf-8")

    status = read_genau_status(status_file)

    assert status.cruise_active is False
    assert status.shape == "sawtooth"


def test_read_nau_status_parses_has_funscript(tmp_path: Path):
    # Nau publishes has_funscript per current video; the hybrid handoff arbiter
    # reads it to decide whether the funscript or Genau drives the OSR2.
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nposition_ms=567\nhas_funscript=1\nstate=normal\npaused=0\n",
        encoding="utf-8",
    )

    status = read_nau_status(status_file)

    assert status.has_funscript is True


def test_read_nau_status_defaults_has_funscript_to_false(tmp_path: Path):
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text("video=C:\\clip.mp4\nhas_funscript=0\n", encoding="utf-8")

    assert read_nau_status(status_file).has_funscript is False
    assert read_nau_status(tmp_path / "missing.txt").has_funscript is False


def test_read_nau_status_parses_funscript_resting(tmp_path: Path):
    # Nau flags when the current spot is in a funscript gap so the hybrid arbiter
    # can hand that stretch to Genau.
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nhas_funscript=1\nfunscript_resting=1\n", encoding="utf-8"
    )

    assert read_nau_status(status_file).funscript_resting is True


def test_read_nau_status_defaults_funscript_resting_to_false(tmp_path: Path):
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text("video=C:\\clip.mp4\nhas_funscript=1\n", encoding="utf-8")

    assert read_nau_status(status_file).funscript_resting is False
    assert read_nau_status(tmp_path / "missing.txt").funscript_resting is False


def test_read_nau_status_parses_the_lock(tmp_path: Path):
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text("video=C:\\clip.mp4\nlocked=0\n", encoding="utf-8")

    assert read_nau_status(status_file).locked is False


def test_read_nau_status_defaults_the_lock_to_on(tmp_path: Path):
    """Holding one video is what the main player does until told otherwise, so a
    status that says nothing about the lock — or no status at all — must not read
    as unlocked and light the console's padlock the wrong way."""
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text("video=C:\\clip.mp4\n", encoding="utf-8")

    assert read_nau_status(status_file).locked is True
    assert read_nau_status(tmp_path / "missing.txt").locked is True


def test_read_nau_status_parses_position_and_duration(tmp_path: Path):
    # Watch tracking (breeding) needs the playback fraction, so both the
    # position and the clip length are read off Nau's status file.
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nposition_ms=54233\nduration_ms=60000\nstate=normal\npaused=0\n",
        encoding="utf-8",
    )

    status = read_nau_status(status_file)

    assert status.position_ms == 54233
    assert status.duration_ms == 60000


def test_read_nau_status_defaults_duration_to_zero(tmp_path: Path):
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text("video=C:\\clip.mp4\n", encoding="utf-8")

    assert read_nau_status(status_file).duration_ms == 0
    assert read_nau_status(tmp_path / "missing.txt").duration_ms == 0


def test_read_genau_status_names_the_clip_on_screen(tmp_path: Path):
    """Genau rescans its folder every launch and opens at the top of it, so the
    clip it was left showing survives only by being published and handed back."""
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("cruise=0\nclip=C:\\clips\\alpha.mp4\n", encoding="utf-8")

    assert read_genau_status(status_file).clip == "C:\\clips\\alpha.mp4"


def test_read_genau_status_reads_no_clip_before_one_is_up(tmp_path: Path):
    """Genau publishes from its refresh loop, which can run before the first clip
    is decoded — and an older Genau does not publish the key at all."""
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("cruise=0\nclip=\n", encoding="utf-8")

    assert read_genau_status(status_file).clip == ""
    assert read_genau_status(tmp_path / "missing.txt").clip == ""


def test_read_nau_status_parses_the_range_a_running_loop_holds(tmp_path: Path):
    """A loop lives in the player process, so the only record of one is what Nau
    publishes — which is how a reopened session can be handed it back."""
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nstate=looping\nloop_in_ms=2000\nloop_out_ms=4000\n",
        encoding="utf-8",
    )

    assert read_nau_status(status_file).loop_bounds == (2000, 4000)


def test_read_nau_status_reads_no_loop_where_nothing_is_looping(tmp_path: Path):
    """The bounds go on being published as the empty range when the loop is
    cancelled, and an older Nau does not publish them at all — neither is a loop
    to come back to."""
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nstate=normal\nloop_in_ms=0\nloop_out_ms=0\n", encoding="utf-8",
    )

    assert read_nau_status(status_file).loop_bounds is None
    assert read_nau_status(tmp_path / "missing.txt").loop_bounds is None


def test_a_loop_state_without_bounds_is_no_loop(tmp_path: Path):
    """Nau published the state before it published the range, so a status file
    left by that version names a loop it cannot describe.  Sending mpv a
    zero-length A/B range would strand the video on one frame."""
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text("video=C:\\clip.mp4\nstate=looping\n", encoding="utf-8")

    assert read_nau_status(status_file).loop_bounds is None


def test_funscript_driving_is_scripted_and_not_resting():
    assert NauStatus(has_funscript=True, funscript_resting=False).funscript_driving is True
    assert NauStatus(has_funscript=True, funscript_resting=True).funscript_driving is False
    assert NauStatus(has_funscript=False).funscript_driving is False




class TestTheTwoFreshnessChecks:
    """One check, two windows.  They were the same four statements twice,
    differing only in the window and in whether the boundary counts."""

    def _stamped(self, tmp_path, age: float):
        path = tmp_path / "stamp.txt"
        path.write_text(str(1000.0 - age), encoding="utf-8")
        return path

    def test_the_device_counts_as_on_inside_its_window_and_not_on_it(self, tmp_path):
        from fun_time.player_status import is_osr2_device_on

        assert is_osr2_device_on(self._stamped(tmp_path, 15.9), now=1000.0) is True
        assert is_osr2_device_on(self._stamped(tmp_path, 16.0), now=1000.0) is False

    def test_the_heartbeat_counts_as_fresh_on_its_boundary(self, tmp_path):
        """The one difference between them, kept: `<=`, not `<`."""
        from fun_time.player_status import is_broker_heartbeat_fresh

        assert is_broker_heartbeat_fresh(self._stamped(tmp_path, 3.0), now=1000.0) is True
        assert is_broker_heartbeat_fresh(self._stamped(tmp_path, 3.1), now=1000.0) is False

    def test_a_stamp_that_is_not_there_or_is_not_a_number_is_neither(self, tmp_path):
        from fun_time.player_status import is_broker_heartbeat_fresh, is_osr2_device_on

        missing = tmp_path / "never_written.txt"
        garbled = tmp_path / "garbled.txt"
        garbled.write_text("not a timestamp", encoding="utf-8")

        assert is_osr2_device_on(missing, now=1000.0) is False
        assert is_broker_heartbeat_fresh(garbled, now=1000.0) is False


class TestTheStatusFilesShape:
    def test_a_line_with_no_equals_is_passed_over(self, tmp_path):
        """Both players' files are read by this one parse; a torn write leaves
        a fragment that is not a pair, and it must not take the read down."""
        from fun_time.player_status import read_key_values

        path = tmp_path / "status.txt"
        path.write_text("video=demo.mp4\nnonsense\nposition_ms=42\n", encoding="utf-8")

        assert read_key_values(path) == {"video": "demo.mp4", "position_ms": "42"}

    def test_only_the_first_equals_separates(self, tmp_path):
        """A path with an '=' in it is a value, not a second key."""
        from fun_time.player_status import read_key_values

        path = tmp_path / "status.txt"
        path.write_text("video=C:/clips/a=b.mp4\n", encoding="utf-8")

        assert read_key_values(path) == {"video": "C:/clips/a=b.mp4"}
