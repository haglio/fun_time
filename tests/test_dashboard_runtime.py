from __future__ import annotations

from pathlib import Path

from fun_time.dashboard_bridge import DashboardSnapshot, write_dashboard_snapshot
from fun_time.dashboard_runtime import load_dashboard_snapshot
from fun_time.funestra_status import (
    GenauStatus,
    MainFunestraStatus,
    origenerator_has_published,
    published_genau_status,
    published_main_funestra_status,
    read_genau_status,
    read_key_values,
    read_main_funestra_status,
)


def _a_snapshot_todays_writer_cannot_produce(tmp_path: Path, text: str) -> Path:
    snapshot_file = tmp_path / "dashboard_state.ini"
    snapshot_file.write_text(text, encoding="utf-8")
    return snapshot_file


def test_a_snapshot_that_was_never_written_reads_as_no_snapshot(tmp_path: Path):
    assert load_dashboard_snapshot(tmp_path / "missing.ini") is None


def test_load_dashboard_snapshot_reads_omnipause_state(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    write_dashboard_snapshot(snapshot_file, DashboardSnapshot(omni_paused=True))

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.omni_paused is True


def test_an_older_writers_snapshot_with_no_omnipause_section_reads_as_playing(tmp_path: Path):
    snapshot_file = _a_snapshot_todays_writer_cannot_produce(tmp_path, "[voice]\nactive=1\n")

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.omni_paused is False


def test_load_dashboard_snapshot_reads_voice_active(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    write_dashboard_snapshot(snapshot_file, DashboardSnapshot(voice_active=False))

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.voice_active is False


def test_an_older_writers_snapshot_with_no_voice_section_reads_as_listening(tmp_path: Path):
    snapshot_file = _a_snapshot_todays_writer_cannot_produce(tmp_path, "[omnipause]\nactive=0\n")

    snapshot = load_dashboard_snapshot(snapshot_file)

    assert snapshot is not None
    assert snapshot.voice_active is True


def test_load_dashboard_snapshot_reads_whether_every_funestra_has_nothing_to_reset(tmp_path: Path):
    snapshot_file = tmp_path / "dashboard_state.ini"
    write_dashboard_snapshot(snapshot_file, DashboardSnapshot(nothing_to_reset=True))

    assert load_dashboard_snapshot(snapshot_file).nothing_to_reset is True


def test_a_genau_that_has_not_reported_yet_reads_at_the_defaults(tmp_path: Path):
    status = read_genau_status(tmp_path / "missing.txt")

    assert status == GenauStatus()
    assert status.shape == "sine"


def test_read_genau_status_parses_the_shape(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("shape=triangle\n", encoding="utf-8")

    assert read_genau_status(status_file).shape == "triangle"


def test_read_genau_status_parses_the_learned_motion(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("learned=1\n", encoding="utf-8")

    assert read_genau_status(status_file).learned_active is True
    assert read_genau_status(tmp_path / "missing.txt").learned_active is False


def test_read_genau_status_parses_the_flick_lock(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("locked=0\n", encoding="utf-8")

    assert read_genau_status(status_file).locked is False


def test_a_genau_status_silent_about_the_lock_reads_locked_as_genau_opens(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("shape=sine\n", encoding="utf-8")

    assert read_genau_status(status_file).locked is True
    assert read_genau_status(tmp_path / "missing.txt").locked is True


def test_read_main_funestra_status_parses_has_funscript(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nposition_ms=567\nhas_funscript=1\nloop_state=normal\npaused=0\n",
        encoding="utf-8",
    )

    status = read_main_funestra_status(status_file)

    assert status.has_funscript is True


def test_read_main_funestra_status_defaults_has_funscript_to_false(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text("video=C:\\clip.mp4\nhas_funscript=0\n", encoding="utf-8")

    assert read_main_funestra_status(status_file).has_funscript is False
    assert read_main_funestra_status(tmp_path / "missing.txt").has_funscript is False


def test_read_main_funestra_status_parses_funscript_resting(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nhas_funscript=1\nfunscript_resting=1\n", encoding="utf-8"
    )

    assert read_main_funestra_status(status_file).funscript_resting is True


def test_read_main_funestra_status_defaults_funscript_resting_to_false(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text("video=C:\\clip.mp4\nhas_funscript=1\n", encoding="utf-8")

    assert read_main_funestra_status(status_file).funscript_resting is False
    assert read_main_funestra_status(tmp_path / "missing.txt").funscript_resting is False


def test_read_main_funestra_status_parses_the_lock(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text("video=C:\\clip.mp4\nlocked=0\n", encoding="utf-8")

    assert read_main_funestra_status(status_file).locked is False


def test_a_main_status_silent_about_the_lock_reads_locked_as_the_main_funestra_opens(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text("video=C:\\clip.mp4\n", encoding="utf-8")

    assert read_main_funestra_status(status_file).locked is True
    assert read_main_funestra_status(tmp_path / "missing.txt").locked is True


def test_read_main_funestra_status_parses_position_and_duration(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nposition_ms=54233\nduration_ms=60000\nloop_state=normal\npaused=0\n",
        encoding="utf-8",
    )

    status = read_main_funestra_status(status_file)

    assert status.position_ms == 54233
    assert status.duration_ms == 60000


def test_read_main_funestra_status_defaults_duration_to_zero(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text("video=C:\\clip.mp4\n", encoding="utf-8")

    assert read_main_funestra_status(status_file).duration_ms == 0
    assert read_main_funestra_status(tmp_path / "missing.txt").duration_ms == 0


def test_read_main_funestra_status_parses_the_rate_the_main_funestra_plays_at(tmp_path: Path):
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text("video=C:\\clip.mp4\nspeed=1.5\n", encoding="utf-8")

    assert read_main_funestra_status(status_file).speed == 1.5


def test_read_main_funestra_status_reads_no_published_rate_as_normal_speed(tmp_path: Path):
    status_file = tmp_path / "nau_status.txt"
    status_file.write_text("video=C:\\clip.mp4\nhas_funscript=1\n", encoding="utf-8")

    assert read_main_funestra_status(status_file).speed == 1.0
    assert read_main_funestra_status(status_file).has_funscript is True
    assert read_main_funestra_status(tmp_path / "missing.txt").speed == 1.0


def test_read_genau_status_names_the_flick_on_screen(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("flick=C:\\flicks\\alpha.mp4\n", encoding="utf-8")

    assert read_genau_status(status_file).flick == "C:\\flicks\\alpha.mp4"


def test_read_genau_status_reads_no_flick_before_one_is_up(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("flick=\n", encoding="utf-8")

    assert read_genau_status(status_file).flick == ""
    assert read_genau_status(tmp_path / "missing.txt").flick == ""


def test_read_genau_status_says_whether_the_flick_up_is_flipped(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("flick=C:\\flicks\\alpha.mp4\nflipped=1\n", encoding="utf-8")

    assert read_genau_status(status_file).flipped is True
    assert read_genau_status(tmp_path / "missing.txt").flipped is False


def test_read_main_funestra_status_parses_the_range_a_running_loop_holds(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nloop_state=looping\nloop_in_ms=2000\nloop_out_ms=4000\n",
        encoding="utf-8",
    )

    assert read_main_funestra_status(status_file).loop_bounds == (2000, 4000)


def test_the_empty_range_a_canceled_loop_goes_on_publishing_is_no_loop(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nloop_state=normal\nloop_in_ms=0\nloop_out_ms=0\n", encoding="utf-8",
    )

    assert read_main_funestra_status(status_file).loop_bounds is None
    assert read_main_funestra_status(tmp_path / "missing.txt").loop_bounds is None


def test_a_loop_an_older_main_funestra_published_without_its_range_is_no_loop(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text("video=C:\\clip.mp4\nloop_state=looping\n", encoding="utf-8")

    assert read_main_funestra_status(status_file).loop_bounds is None


def test_read_main_funestra_status_parses_what_a_reset_puts_back(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text(
        "video=C:\\clip.mp4\nspeed=1.25\ncompilation=Example Studio Volume One\n",
        encoding="utf-8",
    )

    status = read_main_funestra_status(status_file)

    assert (status.speed, status.compilation) == (1.25, "Example Studio Volume One")
    assert read_main_funestra_status(tmp_path / "missing.txt").speed == 1.0


def test_funscript_driving_is_scripted_and_not_resting():
    assert MainFunestraStatus(has_funscript=True, funscript_resting=False).funscript_driving is True
    assert MainFunestraStatus(has_funscript=True, funscript_resting=True).funscript_driving is False
    assert MainFunestraStatus(has_funscript=False).funscript_driving is False


class TestTheStatusFilesShape:
    def test_the_fragment_a_torn_write_leaves_is_passed_over(self, tmp_path):
        path = tmp_path / "status.txt"
        path.write_text("video=demo.mp4\nnonsense\nposition_ms=42\n", encoding="utf-8")

        assert read_key_values(path) == {"video": "demo.mp4", "position_ms": "42"}

    def test_only_the_first_equals_separates(self, tmp_path):
        path = tmp_path / "status.txt"
        path.write_text("video=C:/clips/a=b.mp4\n", encoding="utf-8")

        assert read_key_values(path) == {"video": "C:/clips/a=b.mp4"}


def test_the_main_funestras_video_is_portrait_where_it_says_so(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text("video=C:/v/n.mp4\nportrait=1\n", encoding="utf-8")

    assert read_main_funestra_status(status_file).portrait is True


def test_genaus_flick_is_portrait_where_it_says_so(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("flick=C:/flicks/alpha.mp4\nportrait=1\n", encoding="utf-8")

    assert read_genau_status(status_file).portrait is True


def test_a_main_status_that_cannot_be_read_gives_back_the_last_good_one(tmp_path: Path):
    last_good = MainFunestraStatus(speed=1.5)

    assert read_main_funestra_status(tmp_path, fallback=last_good) is last_good
    assert read_main_funestra_status(tmp_path / "missing.txt", fallback=last_good) is last_good


def test_a_main_status_that_cannot_be_read_and_has_no_last_good_one_reads_as_the_defaults(tmp_path: Path):
    assert read_main_funestra_status(tmp_path) == MainFunestraStatus()


def test_a_genau_status_that_cannot_be_read_reads_as_the_defaults(tmp_path: Path):
    assert read_genau_status(tmp_path) == GenauStatus()


def test_a_main_status_without_a_speed_has_not_been_published_yet(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text("video=clip.mp4\n", encoding="utf-8")

    assert published_main_funestra_status(status_file) is None
    assert published_main_funestra_status(tmp_path) is None
    status_file.write_text("speed=1.0\n", encoding="utf-8")
    assert published_main_funestra_status(status_file) is not None


def test_a_genau_status_without_a_lock_has_not_been_published_yet(tmp_path: Path):
    status_file = tmp_path / "genau_status.txt"
    status_file.write_text("shape=sine\n", encoding="utf-8")

    assert published_genau_status(status_file) is None
    assert published_genau_status(tmp_path) is None
    status_file.write_text("locked=0\n", encoding="utf-8")
    assert published_genau_status(status_file) == GenauStatus(locked=False)


def test_the_hosted_app_has_published_once_its_status_file_can_be_read(tmp_path: Path):
    status_file = tmp_path / "origenerator_status.txt"

    assert origenerator_has_published(status_file) is False
    assert origenerator_has_published(tmp_path) is False
    status_file.write_text("", encoding="utf-8")
    assert origenerator_has_published(status_file) is True


def test_a_status_file_caught_half_written_is_not_yet_published(tmp_path: Path):
    status_file = tmp_path / "origenerator_status.txt"
    half_a_character = "ready=\u00e9".encode()[:-1]
    status_file.write_bytes(half_a_character)

    assert origenerator_has_published(status_file) is False
def test_a_range_with_nothing_looping_is_no_loop(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text(
        "video=C:/clip.mp4\nloop_state=normal\nloop_in_ms=2000\nloop_out_ms=4000\n",
        encoding="utf-8",
    )

    assert read_main_funestra_status(status_file).loop_bounds is None


def test_a_looping_state_with_an_empty_range_is_no_loop(tmp_path: Path):
    status_file = tmp_path / "main_player_status.txt"
    status_file.write_text(
        "video=C:/clip.mp4\nloop_state=looping\nloop_in_ms=3000\nloop_out_ms=3000\n",
        encoding="utf-8",
    )

    assert read_main_funestra_status(status_file).loop_bounds is None
