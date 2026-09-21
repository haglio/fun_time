"""What this app reads off a library video's record, held to Evolver's promise.

Evolver writes that record; this app reads one on every clip it shows and
writes one field back. Both spelled every block and field name for themselves,
and nothing compared them: a key renamed there left this app quietly answering
"nothing recorded" about a clip whose record was right there. Walking up to
evolver_contract.json is the one place the two can be compared.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fun_time import media_metadata

CONTRACT = Path("evolver") / "evolver_contract.json"


def _record() -> dict:
    for parent in Path(__file__).resolve().parents:
        published = parent / CONTRACT
        if published.is_file():
            document = json.loads(published.read_text(encoding="utf-8"))
            return document["library_record"]
    pytest.skip(f"no {CONTRACT.as_posix()} beside this checkout")


def _fields(block: str) -> set[str]:
    return set(_record()["blocks"][block]["fields"])


def test_the_kind_this_app_bands_clips_by_is_one_evolver_records():
    """A kind spelled differently here puts every carved scene in the wrong
    band, silently -- there is no error for a value that simply never matches."""
    assert media_metadata.EXCERPT in _record()["video_kinds"]


def test_the_act_and_its_rejection_are_where_evolver_says():
    """Clearing the act puts a clip back in front of the backfill tool; the
    rejection beside it says it was REJECTED rather than never labeled."""
    record = _record()
    video = record["blocks"]["video"]

    assert media_metadata.WRONG_ACTION_FIELD in video["fields"]
    assert video["written_by_the_reader"] == [media_metadata.WRONG_ACTION_FIELD]
    assert "action" in video["fields"]


def test_every_field_a_clips_identity_is_built_from_is_promised():
    """These decide which clips are kin; a moved field makes each its own."""
    assert set(media_metadata.VIDEO_BASE_FIELDS) <= _fields("video")
    assert set(media_metadata.IMAGE_IDENTITY_FIELDS) <= _fields("source_image")


def test_what_a_clip_is_called_and_what_it_was_cut_from_are_promised():
    record = _record()

    assert media_metadata.TITLE_FIELD in record["top_level_keys"]
    assert {"performer", "source"} <= _fields("clip")
    assert "group" in _fields("version")


def test_the_playback_weight_this_app_draws_by_is_promised():
    """A weight read as missing is a clip shuffled as though nobody had ever
    watched it."""
    assert "weight" in _fields(media_metadata.WATCH_BLOCK)
