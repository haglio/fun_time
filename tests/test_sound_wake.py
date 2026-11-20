from __future__ import annotations

from pathlib import Path

from fun_time.sound_wake import (
    READ_THE_FILES_AGAIN,
    READ_THE_FILES_AT_LEAST_EVERY_S,
    listen_for_the_session,
    tell_the_sound,
)


def test_the_sound_hears_the_session_tell_it_to_read_its_files_again(tmp_path: Path):
    sound = listen_for_the_session("127.0.0.1", 0, tmp_path)

    tell_the_sound(tmp_path)

    word, _ = sound.recvfrom(4096)
    assert word.decode("utf-8") == READ_THE_FILES_AGAIN
    sound.close()


def test_with_nothing_said_the_sound_reads_its_files_again_once_a_second(tmp_path: Path):
    sound = listen_for_the_session("127.0.0.1", 0, tmp_path)

    assert sound.gettimeout() == READ_THE_FILES_AT_LEAST_EVERY_S == 1.0
    sound.close()


def test_telling_a_sound_that_is_not_listening_yet_changes_nothing(tmp_path: Path):
    tell_the_sound(tmp_path)

    assert list(tmp_path.iterdir()) == []
