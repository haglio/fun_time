from __future__ import annotations

import contextlib
import logging
import os
import stat
from pathlib import Path

import pytest

from tests.scratch import scratch_dir, scratch_root_of


def test_a_read_only_file_does_not_keep_the_scratch_standing(tmp_path: Path):
    with scratch_dir(tmp_path) as scratch:
        written = scratch / "object"
        written.write_bytes(b"what git writes under .git/objects")
        written.chmod(stat.S_IREAD)

    assert not scratch.exists()


def test_a_log_the_case_opened_in_its_scratch_is_closed_before_the_scratch_goes(
    tmp_path: Path,
):
    with scratch_dir(tmp_path) as scratch:
        logging.getLogger("a case's own").addHandler(logging.FileHandler(scratch / "case.log"))

    assert not scratch.exists()


def test_a_file_the_case_left_open_is_named_instead_of_left_behind(tmp_path: Path):
    with (
        contextlib.ExitStack() as still_open,
        pytest.raises(PermissionError, match=r"held\.log"),
        scratch_dir(tmp_path) as scratch,
    ):
        still_open.enter_context((scratch / "held.log").open("w"))


def test_a_library_video_linked_in_keeps_its_read_only_bit(tmp_path: Path):
    outside = tmp_path / "library.mp4"
    outside.write_bytes(b"a video its library keeps read-only")
    outside.chmod(stat.S_IREAD)
    try:
        with (
            pytest.raises(PermissionError, match=r"linked\.mp4"),
            scratch_dir(tmp_path) as scratch,
        ):
            os.link(outside, scratch / "linked.mp4")

        assert not outside.stat().st_mode & stat.S_IWRITE
    finally:
        outside.chmod(stat.S_IWRITE)


def test_a_worker_of_a_run_side_by_side_keeps_its_scratch_in_a_folder_of_its_own():
    assert scratch_root_of(Path("everyone's"), {"PYTEST_XDIST_WORKER": "gw3"}) == Path("everyone's") / "gw3"


def test_a_run_in_one_process_keeps_its_scratch_where_it_always_did():
    assert scratch_root_of(Path("everyone's"), {}) == Path("everyone's")
