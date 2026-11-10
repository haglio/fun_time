from __future__ import annotations

import configparser
from pathlib import Path

import pytest

from fun_time.dashboard_bridge import (
    DashboardSnapshot,
    build_dashboard_snapshot_text,
    decode_snapshot,
    write_dashboard_snapshot,
)
from fun_time.dashboard_runtime import load_dashboard_snapshot
from fun_time.rfb_modes import RfbMode


def test_the_writer_and_the_reader_speak_one_record(tmp_path: Path):
    snapshot = DashboardSnapshot(
        omni_paused=True, voice_active=False, f_mode=True, in_vr=True, nothing_to_reset=True,
        origenerator_ready=True, rfb_mode=RfbMode.ORIGENERATOR,
    )
    path = tmp_path / "dashboard_state.ini"

    write_dashboard_snapshot(path, snapshot)

    assert load_dashboard_snapshot(path) == snapshot


def test_the_snapshot_published_each_tick_carries_only_what_the_dashboard_reads():
    parser = configparser.ConfigParser()
    parser.read_string(build_dashboard_snapshot_text())

    assert set(parser.sections()) == {
        "omnipause", "voice", "fmode", "reset", "session", "headset", "origenerator"}


def test_build_dashboard_snapshot_text_matches_bridge_contract():
    text = build_dashboard_snapshot_text()

    assert text == (
        "[omnipause]\n"
        "active=0\n"
        "[voice]\n"
        "active=1\n"
        "[fmode]\n"
        "active=0\n"
        "[reset]\n"
        "nothing=0\n"
        "[session]\n"
        "vr=0\n"
        "[headset]\n"
        "ready=1\n"
        "[origenerator]\n"
        "ready=0\n"
        "in_the_rfb_mode=0\n"
    )


def test_build_dashboard_snapshot_text_includes_omnipause_state():
    text = build_dashboard_snapshot_text(DashboardSnapshot(omni_paused=True))

    assert "[omnipause]\nactive=1\n" in text


def test_build_dashboard_snapshot_text_includes_voice_state():
    text = build_dashboard_snapshot_text(DashboardSnapshot(voice_active=False))

    assert "[voice]\nactive=0\n" in text


def test_the_snapshot_says_when_every_funestra_has_nothing_to_reset():
    assert "[reset]\nnothing=1\n" in build_dashboard_snapshot_text(DashboardSnapshot(nothing_to_reset=True))
    assert "[reset]\nnothing=0\n" in build_dashboard_snapshot_text()


def test_write_dashboard_snapshot_writes_utf16_and_skips_identical_content(tmp_path: Path):
    output = tmp_path / "dashboard_state.ini"

    first = write_dashboard_snapshot(output, DashboardSnapshot(omni_paused=True))
    second = write_dashboard_snapshot(output, DashboardSnapshot(omni_paused=True))

    assert first is True
    assert second is False
    text = output.read_text(encoding="utf-16")
    assert "[omnipause]" in text
    assert "active=1" in text


class TestTheSnapshotsEncoding:
    def test_what_the_writer_wrote_reads_back(self, tmp_path):
        path = tmp_path / "dashboard_state.ini"
        write_dashboard_snapshot(path, DashboardSnapshot(omni_paused=True))

        assert decode_snapshot(path.read_bytes()) == build_dashboard_snapshot_text(DashboardSnapshot(omni_paused=True))

    def test_the_decoder_normalizes_the_newlines_text_mode_writes(self):
        assert decode_snapshot("[voice]\r\nactive=1\r\n".encode("utf-16")) == (
            "[voice]\nactive=1\n")

    @pytest.mark.parametrize("encoding", ["utf-16", "utf-8", "utf-8-sig"])
    def test_an_older_sessions_file_is_still_readable(self, tmp_path, encoding):
        assert decode_snapshot("[voice]\nactive=1\n".encode(encoding)) == (
            "[voice]\nactive=1\n")

    def test_bytes_in_none_of_those_say_so(self):
        with pytest.raises(UnicodeDecodeError):
            decode_snapshot(b"\xff\xfe\xfd")

    def test_this_satellite_never_fails_a_write_over_an_unreadable_file(self, tmp_path):
        path = tmp_path / "dashboard_state.ini"
        path.write_bytes(b"\xff\xfe\xfd")

        assert write_dashboard_snapshot(path, DashboardSnapshot(omni_paused=True)) is True


class TestTheWriterSparesTheLocklessReaderARewrite:
    def _write_as_windows_would(self, path: Path, text: str) -> None:
        with open(path, "w", encoding="utf-16", newline="\r\n") as handle:
            handle.write(text)

    def test_an_identical_snapshot_is_not_rewritten(self, tmp_path):
        path = tmp_path / "dashboard_state.ini"

        assert write_dashboard_snapshot(path, DashboardSnapshot(omni_paused=True)) is True
        assert write_dashboard_snapshot(path, DashboardSnapshot(omni_paused=True)) is False

    def test_nor_when_the_file_carries_the_line_endings_windows_gave_it(self, tmp_path):
        path = tmp_path / "dashboard_state.ini"
        self._write_as_windows_would(path, build_dashboard_snapshot_text(DashboardSnapshot(omni_paused=True)))

        assert write_dashboard_snapshot(path, DashboardSnapshot(omni_paused=True)) is False

    def test_a_snapshot_that_did_change_is_written(self, tmp_path):
        path = tmp_path / "dashboard_state.ini"
        self._write_as_windows_would(path, build_dashboard_snapshot_text(DashboardSnapshot(omni_paused=True)))

        assert write_dashboard_snapshot(path, DashboardSnapshot(omni_paused=False)) is True
def test_whether_the_headset_is_ready_survives_the_round_trip(tmp_path):
    state_file = tmp_path / "dashboard_state.ini"
    write_dashboard_snapshot(state_file, DashboardSnapshot(headset_ready=True))
    assert load_dashboard_snapshot(state_file).headset_ready is True

    write_dashboard_snapshot(state_file, DashboardSnapshot(headset_ready=False))
    assert load_dashboard_snapshot(state_file).headset_ready is False


def test_a_snapshot_from_a_session_that_never_knew_offers_the_crossing(tmp_path):
    state_file = tmp_path / "dashboard_state.ini"
    state_file.write_text("[session]\nvr=0\n", encoding="utf-8")

    assert load_dashboard_snapshot(state_file).headset_ready is True
