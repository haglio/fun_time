"""A state directory written before 2026-09-11 holds the main player's files under
its old name; a session opening on it must resume from them, not from nothing."""
from __future__ import annotations

from pathlib import Path

from fun_time.state_file_names import take_up_the_retired_state_file_names


def _left_by_an_older_session(state_dir: Path) -> dict[str, str]:
    files = {
        "nau_cmd.txt": "SET_ACTIVE 1\n",
        "nau_paused.txt": "0",
        "nau_status.txt": "video=C:/example/library/alpha clip.mp4\n",
        "nau_playlist.tsv": "C:/example/library/alpha clip.mp4\n",
        "nau_durations.json": "{}",
        "nau_mode.txt": "length_mode=mixed\n",
        "nau.log": "started\n",
    }
    for name, body in files.items():
        (state_dir / name).write_text(body, encoding="utf-8")
    return files


def test_every_file_of_the_old_name_comes_back_under_the_new_one(tmp_path: Path):
    files = _left_by_an_older_session(tmp_path)

    taken_up = take_up_the_retired_state_file_names(tmp_path)

    assert sorted(p.name for p in taken_up) == sorted(
        "main_player" + name[len("nau"):] for name in files)
    for name, body in files.items():
        assert not (tmp_path / name).exists()
        assert (tmp_path / ("main_player" + name[len("nau"):])).read_text(encoding="utf-8") == body


def test_a_file_already_under_the_new_name_is_not_replaced_by_the_old_one(tmp_path: Path):
    """The old copy is from a session that ended before the new name existed, so
    a newer session's file always says more."""
    (tmp_path / "nau_status.txt").write_text("video=old.mp4\n", encoding="utf-8")
    (tmp_path / "main_player_status.txt").write_text("video=new.mp4\n", encoding="utf-8")

    assert take_up_the_retired_state_file_names(tmp_path) == []
    assert (tmp_path / "main_player_status.txt").read_text(encoding="utf-8") == "video=new.mp4\n"
    assert (tmp_path / "nau_status.txt").exists()


def test_the_other_files_in_the_directory_are_left_alone(tmp_path: Path):
    (tmp_path / "genau_status.txt").write_text("clip=beta\n", encoding="utf-8")
    (tmp_path / "portrait_status.txt").write_text("video=\n", encoding="utf-8")
    (tmp_path / "nau_thumbs").mkdir()

    assert take_up_the_retired_state_file_names(tmp_path) == []
    assert (tmp_path / "genau_status.txt").exists()
    assert (tmp_path / "portrait_status.txt").exists()
    assert (tmp_path / "nau_thumbs").is_dir()


def test_a_directory_with_nothing_old_in_it_is_a_no_op(tmp_path: Path):
    assert take_up_the_retired_state_file_names(tmp_path) == []
