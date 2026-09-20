"""The step that puts another version of a clip on a satellite, as it travels.

The source names the whole family with every step, so the player steps from the
file it has up rather than from the one the source last read off a status file.
"""
from __future__ import annotations

from pathlib import Path

from satellite.versions import NEXT_VERSION, PREV_VERSION, step_version, version_files


def test_a_step_forward_carries_the_family_in_the_sources_order():
    versions = [Path("C:/vids/clip_topaz.mp4"), Path("C:/vids/clip.mp4")]

    line = step_version(1, versions)

    keyword, _, value = line.partition(" ")
    assert keyword == NEXT_VERSION
    assert version_files(value) == versions


def test_a_step_back_is_the_other_verb():
    line = step_version(-1, [Path("C:/vids/clip_topaz.mp4"), Path("C:/vids/clip.mp4")])

    assert line.partition(" ")[0] == PREV_VERSION


def test_a_value_naming_nothing_carries_no_versions():
    assert version_files("") == []
