from __future__ import annotations

from fun_time.append_only import append_line


def test_a_line_lands_after_the_ones_already_there(tmp_path):
    log = tmp_path / "windows_bridge.log"

    append_line(log, "first\r\n")
    append_line(log, "second\r\n")

    assert log.read_bytes() == b"first\r\nsecond\r\n"
