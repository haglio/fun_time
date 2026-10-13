from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from funestra.funestra_status import status_fields
from funestra.status import StatusWriter

from fun_time.windows_bridge_orchestrator import ChildProcess
from tests.integration import integration_support, lone_satellite
from tests.integration.integration_support import keep_what_the_run_logged
from tests.integration.lone_satellite import (
    LoneSatellite,
    a_lone_satellite,
    end_satellite,
    identify_child,
)


@pytest.fixture(autouse=True)
def _no_roots_left_between_tests():
    integration_support.RUN_ROOTS.clear()
    yield
    integration_support.RUN_ROOTS.clear()


@pytest.fixture
def killed():
    with patch.object(lone_satellite, "kill_recorded_child") as kill:
        yield kill


def _publishes(satellite: LoneSatellite, *, video: str, position_ms: int, duration_ms: int,
               playlist_length: int) -> None:
    playback = SimpleNamespace(
        current_video=Path(video), position_ms=position_ms, duration_ms=duration_ms,
        is_paused=False, is_locked=False, speed=1.0, showing_picture=False,
        playlist_length=playlist_length, has_funscript=False, funscript_resting=False,
        portrait=None)
    StatusWriter(satellite.status, lambda played: status_fields(played, None)).write(playback)


def test_a_launched_child_is_named_by_its_pid_and_the_moment_it_was_born():
    with patch.object(lone_satellite, "get_process_creation_time", return_value=4200) as born:
        assert identify_child(4242) == ChildProcess(pid=4242, created_at=4200)

    born.assert_called_once_with(4242)


def test_a_child_already_gone_when_named_is_one_no_live_process_can_match():
    with patch.object(lone_satellite, "get_process_creation_time", return_value=None):
        assert identify_child(4242) == ChildProcess(pid=4242, created_at=0)


def test_a_satellite_that_never_lets_go_of_its_log_is_named(tmp_path: Path, killed):
    log = tmp_path / "portrait_satellite.log"

    with log.open("a"), pytest.raises(AssertionError, match=r"pid 4242\) still holds .*portrait_satellite.log"):
        end_satellite(ChildProcess(pid=4242, created_at=1), log, budget_s=0)


def test_a_satellite_on_its_way_out_is_waited_for_until_it_lets_go_of_its_log(tmp_path: Path, killed):
    log = tmp_path / "portrait_satellite.log"
    satellite = ChildProcess(pid=4242, created_at=1)

    with log.open("a") as held:
        end_satellite(satellite, log, budget_s=60.0, sleep=lambda _seconds: held.close())

    killed.assert_called_once_with(satellite)
    assert held.closed


def test_a_satellites_log_is_still_there_after_its_end(tmp_path: Path, killed):
    log = tmp_path / "portrait_satellite.log"
    log.write_text("what it said", encoding="utf-8")

    end_satellite(ChildProcess(pid=4242, created_at=1), log)

    assert log.read_text(encoding="utf-8") == "what it said"


def test_what_a_lone_satellite_logged_is_kept_with_the_runs_logs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    satellite = a_lone_satellite()
    satellite.log.write_text("Satellite playing 3 clip(s)", encoding="utf-8")

    kept = keep_what_the_run_logged(tmp_path / "kept")

    assert [log.read_text(encoding="utf-8") for log in kept.rglob(satellite.log.name)] == [
        "Satellite playing 3 clip(s)"]


def test_what_a_lone_satellite_plays_from_its_own_folder_is_not_kept(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    satellite = a_lone_satellite()
    (satellite.sources / "clip0.mp4").write_bytes(b"not really a video")

    kept = keep_what_the_run_logged(tmp_path / "kept")

    assert not list(kept.rglob("clip0.mp4"))


def test_a_satellite_that_never_published_says_so_and_where_its_log_is(tmp_path: Path):
    satellite = LoneSatellite(tmp_path)

    said = satellite.what_it_last_said()

    assert "never published" in said
    assert str(satellite.log) in said


def test_a_satellite_says_where_its_clip_had_got_to_and_never_which_clip(tmp_path: Path):
    satellite = LoneSatellite(tmp_path)
    _publishes(satellite, video="C:/videos/Jane Doe scene one.mp4", position_ms=0,
               duration_ms=7546, playlist_length=3)

    said = satellite.what_it_last_said()

    assert "at 0 of 7546 ms, not paused, 3 in its list" in said
    assert "Jane Doe" not in said
    assert "scene one" not in said


def test_a_satellite_whose_process_is_gone_says_so(tmp_path: Path):
    satellite = LoneSatellite(tmp_path)
    satellite.process = ChildProcess(pid=4242, created_at=1)

    with patch.object(lone_satellite, "get_process_creation_time", return_value=None):
        assert "its process has ended" in satellite.what_it_last_said()


def test_a_satellite_still_running_says_so(tmp_path: Path):
    satellite = LoneSatellite(tmp_path)
    satellite.process = ChildProcess(pid=4242, created_at=1)

    with patch.object(lone_satellite, "get_process_creation_time", return_value=1), \
            patch.object(lone_satellite, "is_process_alive", return_value=True):
        assert "its process is still running" in satellite.what_it_last_said()
