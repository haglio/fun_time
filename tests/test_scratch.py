"""A case's scratch goes when the case ends, whatever it wrote there."""
from __future__ import annotations

import contextlib
import logging
import os
import stat
from pathlib import Path

import pytest

from tests.scratch import scratch_dir


def test_a_read_only_file_does_not_keep_the_scratch_standing(tmp_path: Path):
    """git writes its object files read-only, and Windows refuses to delete one."""
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
    with contextlib.ExitStack() as still_open:
        with pytest.raises(PermissionError, match="held.log"):
            with scratch_dir(tmp_path) as scratch:
                still_open.enter_context((scratch / "held.log").open("w"))


def test_a_file_linked_in_from_outside_keeps_its_read_only_bit(tmp_path: Path):
    """The integration suite links library videos into its scratch, and every
    link to a file shares that file's read-only bit."""
    outside = tmp_path / "library.mp4"
    outside.write_bytes(b"a video its library keeps read-only")
    outside.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError, match="linked.mp4"):
            with scratch_dir(tmp_path) as scratch:
                os.link(outside, scratch / "linked.mp4")

        assert not outside.stat().st_mode & stat.S_IWRITE
    finally:
        outside.chmod(stat.S_IWRITE)
