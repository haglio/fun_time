from __future__ import annotations

from pathlib import Path

from fun_time.satellite_speeds import SatelliteSpeeds


def _speeds(tmp_path: Path) -> SatelliteSpeeds:
    return SatelliteSpeeds(
        main_player_status_file=tmp_path / "main_player_status.txt",
        satellite_cmd_files=(tmp_path / "portrait_cmd.txt", tmp_path / "landscape_cmd.txt"),
    )


def _main_plays_at(tmp_path: Path, rate: str) -> None:
    (tmp_path / "main_player_status.txt").write_text(
        f"video=C:\\clip.mp4\nspeed={rate}\n", encoding="utf-8")


def _sent(tmp_path: Path, side: str) -> list[str]:
    path = tmp_path / f"{side}_cmd.txt"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_a_change_to_the_main_players_rate_sets_both_satellites_to_it(tmp_path):
    speeds = _speeds(tmp_path)
    _main_plays_at(tmp_path, "1")
    speeds.take_the_main_players_rate()

    _main_plays_at(tmp_path, "1.5")
    speeds.take_the_main_players_rate()

    assert _sent(tmp_path, "portrait") == _sent(tmp_path, "landscape") == ["SET_SPEED 1.5"]


def test_a_rate_the_satellites_were_already_given_is_not_sent_again(tmp_path):
    speeds = _speeds(tmp_path)
    _main_plays_at(tmp_path, "1")
    speeds.take_the_main_players_rate()
    _main_plays_at(tmp_path, "1.5")
    speeds.take_the_main_players_rate()

    speeds.take_the_main_players_rate()

    assert _sent(tmp_path, "portrait") == ["SET_SPEED 1.5"]


def test_a_status_that_cannot_be_read_sends_nothing_and_keeps_the_last_rate(tmp_path):
    speeds = _speeds(tmp_path)
    _main_plays_at(tmp_path, "1")
    speeds.take_the_main_players_rate()
    _main_plays_at(tmp_path, "1.5")
    speeds.take_the_main_players_rate()

    (tmp_path / "main_player_status.txt").unlink()
    speeds.take_the_main_players_rate()
    _main_plays_at(tmp_path, "1.5")
    speeds.take_the_main_players_rate()

    assert _sent(tmp_path, "portrait") == ["SET_SPEED 1.5"]
