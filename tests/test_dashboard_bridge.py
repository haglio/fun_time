from __future__ import annotations

import configparser
from pathlib import Path

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
