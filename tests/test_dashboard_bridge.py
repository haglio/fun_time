from __future__ import annotations

import configparser
from pathlib import Path

import pytest

from fun_time.dashboard_bridge import build_dashboard_snapshot_text, write_dashboard_snapshot


def test_the_snapshot_carries_only_the_sections_the_dashboard_reads():
    """A section nobody reads is republished on every sync tick, forever.

    The dashboard in its own process is the only reader of this file in the
    family, and it asks for exactly two things -- whether the room is
    omnipaused and whether voice is listening.
    """
    parser = configparser.ConfigParser()
    parser.read_string(build_dashboard_snapshot_text())

    assert set(parser.sections()) == {"omnipause", "voice"}


def test_build_dashboard_snapshot_text_matches_bridge_contract():
    text = build_dashboard_snapshot_text()

    assert text == (
        "[omnipause]\n"
        "active=0\n"
        "[voice]\n"
        "active=1\n"
    )


def test_build_dashboard_snapshot_text_includes_omnipause_state():
    text = build_dashboard_snapshot_text(omni_paused=True)

    assert "[omnipause]\nactive=1\n" in text


def test_build_dashboard_snapshot_text_includes_voice_state():
    text = build_dashboard_snapshot_text(voice_active=False)

    assert "[voice]\nactive=0\n" in text


def test_write_dashboard_snapshot_writes_utf16_and_skips_identical_content(tmp_path: Path):
    output = tmp_path / "dashboard_state.ini"

    first = write_dashboard_snapshot(output, omni_paused=True)
    second = write_dashboard_snapshot(output, omni_paused=True)

    assert first is True
    assert second is False
    text = output.read_text(encoding="utf-16")
    assert "[omnipause]" in text
    assert "active=1" in text


class TestTheSnapshotsEncoding:
    """One decoder for a file with one writer and two readers.

    The panel's reader accepted utf-8-sig, utf-16 and utf-8; this side accepted
    utf-16 and utf-8.  Both now ask the module that writes it.
    """

    def test_what_the_writer_wrote_reads_back(self, tmp_path):
        from fun_time.dashboard_bridge import (
            build_dashboard_snapshot_text,
            decode_snapshot,
            write_dashboard_snapshot,
        )

        path = tmp_path / "dashboard_state.ini"
        write_dashboard_snapshot(path, omni_paused=True)

        assert decode_snapshot(path.read_bytes()) == build_dashboard_snapshot_text(
            omni_paused=True)

    @pytest.mark.parametrize("encoding", ["utf-16", "utf-8", "utf-8-sig"])
    def test_an_older_sessions_file_is_still_readable(self, tmp_path, encoding):
        """Both readers took more than the writer emits, and must keep to it."""
        from fun_time.dashboard_bridge import decode_snapshot

        assert decode_snapshot("[voice]\nactive=1\n".encode(encoding)) == (
            "[voice]\nactive=1\n")

    def test_bytes_in_none_of_those_say_so(self):
        from fun_time.dashboard_bridge import decode_snapshot

        with pytest.raises(UnicodeDecodeError):
            decode_snapshot(b"\xff\xfe\xfd")

    def test_this_side_never_fails_a_write_over_an_unreadable_file(self, tmp_path):
        """A snapshot it cannot read is one it has to overwrite, not raise on."""
        from fun_time.dashboard_bridge import write_dashboard_snapshot

        path = tmp_path / "dashboard_state.ini"
        path.write_bytes(b"\xff\xfe\xfd")

        assert write_dashboard_snapshot(path, omni_paused=True) is True
