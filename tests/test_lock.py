"""Unit tests for the native satellite lock model.

Each lock action appends a verb to the satellite's command file — ``LOCK`` to
hold the current clip, ``UNLOCK`` to release it, ``NEXT`` to advance, ``TRASH``
to drop and move on — and the clip an action is about is read from the
satellite's published status file (``read_satellite_status``).

These exercise the three lock helpers in ``command_dispatch`` directly;
``test_command_dispatch`` covers their routing through ``dispatch_command``.  The
shared ``BridgeConfig`` builder and file helpers live there, so they are imported
rather than duplicated.
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

from fun_time.command_dispatch import (
    FAVORITE_NOTICE_LEVEL,
    _cancel_lock,
    _discard,
    _toggle_lock,
)
from fun_time.event_log import NOTICE
from fun_time.media_actions import ensure_in_favs
from tests.test_command_dispatch import _cmds, _make_config, _make_state, _set_current


def test_toggle_lock_locks_and_favorites_when_unlocked(tmp_path: Path, caplog):
    config = _make_config(tmp_path)
    _set_current(config, 2, "clip.mp4")

    with (
        patch("fun_time.command_dispatch.ensure_in_favs") as favs,
        caplog.at_level(logging.INFO, logger="fun_time.command_dispatch"),
    ):
        state, _ops = _toggle_lock(2, _make_state(locked2=False), config)

    assert state.locked2 is True
    # Locking holds the current clip (LOCK) and does not advance past it.
    assert _cmds(config, 2) == ["LOCK"]
    assert favs.call_args[0][1] == "clip.mp4"
    assert "Locked portrait satellite" in caplog.text


def test_toggle_lock_unlocks_and_advances_when_locked(tmp_path: Path, caplog):
    config = _make_config(tmp_path)
    _set_current(config, 3, "clip.mp4")

    with caplog.at_level(logging.INFO, logger="fun_time.command_dispatch"):
        state, _ops = _toggle_lock(3, _make_state(locked3=True), config)

    assert state.locked3 is False
    # Unlocking releases the hold (UNLOCK) and moves on rather than replaying (NEXT).
    assert _cmds(config, 3) == ["UNLOCK", "NEXT"]
    assert "Unlocked landscape satellite" in caplog.text


def test_cancel_lock_writes_unlock_only_when_currently_locked(tmp_path: Path):
    config = _make_config(tmp_path)

    state = _cancel_lock(2, _make_state(locked2=True), config)

    assert state.locked2 is False
    # A locked side is repeat-one; cancelling it queues UNLOCK to restore advance.
    assert _cmds(config, 2) == ["UNLOCK"]


def test_cancel_lock_writes_nothing_when_not_locked(tmp_path: Path):
    config = _make_config(tmp_path)

    state = _cancel_lock(2, _make_state(locked2=False), config)

    assert state.locked2 is False
    # Nothing was locked, so there is no hold to release — no verb is queued.
    assert _cmds(config, 2) == []


def test_locking_a_known_video_opens_an_rfb_tab(tmp_path: Path):
    config = _make_config(tmp_path)
    from fun_time.media_actions import WEB_PROVIDERS

    _set_current(config, 2, rf"C:\videos\{WEB_PROVIDERS[0].marker}\abc_123.mp4")

    with patch("fun_time.command_dispatch.ensure_in_favs"):
        _state, ops = _toggle_lock(2, _make_state(locked2=False), config)

    assert any(op.op == "open_rfb_tab" for op in ops)


def test_unlocking_opens_no_rfb_tab(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_current(config, 2, r"C:\videos\provider2\abc_123.mp4")

    _state, ops = _toggle_lock(2, _make_state(locked2=True), config)

    assert not any(op.op == "open_rfb_tab" for op in ops)


def test_discard_of_a_non_favorite_unlocks_advances_and_moves_to_weird(tmp_path: Path, caplog):
    config = _make_config(tmp_path)
    _set_current(config, 3, "odd.mp4")

    with (
        patch("fun_time.command_dispatch.remove_from_favs") as favs,
        patch("fun_time.command_dispatch.move_to_weird") as weird,
        caplog.at_level(logging.INFO, logger="fun_time.command_dispatch"),
    ):
        state, ops = _discard(3, _make_state(locked3=True), config)

    assert state.locked3 is False
    # A locked discard drops the repeat-one hold (UNLOCK) then trashes the clip,
    # which advances into the playlist (TRASH).
    assert _cmds(config, 3) == ["UNLOCK", "TRASH"]
    assert favs.call_args[0][1] == "odd.mp4"
    assert weird.call_args[0][1] == Path("odd.mp4")
    assert "Discarding from player 3: odd.mp4" in caplog.text
    assert [(op.op, op.key, op.source) for op in ops] == [
        ("notice", "Marked weird", "landscape")
    ]


def test_unlocked_discard_trashes_without_unlocking(tmp_path: Path):
    config = _make_config(tmp_path)
    _set_current(config, 3, "odd.mp4")

    with (
        patch("fun_time.command_dispatch.remove_from_favs"),
        patch("fun_time.command_dispatch.move_to_weird"),
    ):
        state, _ops = _discard(3, _make_state(locked3=False), config)

    assert state.locked3 is False
    # Nothing to release, so discard is a bare TRASH (drop current, play next).
    assert _cmds(config, 3) == ["TRASH"]


def _favorite_clip(tmp_path: Path, config, name: str) -> Path:
    """A real file on disk that is also a row in the favorites list."""
    video = tmp_path / "clips" / name
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    ensure_in_favs(config.favs_file, str(video))
    return video


def test_discard_of_a_favorite_only_takes_it_out_of_the_favorites(tmp_path: Path, caplog):
    """Discarding a favorite demotes it instead of condemning it: the row leaves
    the favs list, and nothing else about the clip changes."""
    config = _make_config(tmp_path)
    video = _favorite_clip(tmp_path, config, "kept.mp4")
    _set_current(config, 3, str(video))

    with caplog.at_level(logging.INFO, logger="fun_time.command_dispatch"):
        state, ops = _discard(3, _make_state(locked3=False), config)

    assert state.locked3 is False
    # NEXT, not TRASH: it moves on from the clip but leaves it in the playlist,
    # so PREV comes straight back to it.
    assert _cmds(config, 3) == ["NEXT"]
    assert str(video) not in config.favs_file.read_text(encoding="utf-8")
    assert video.exists()
    assert list(config.weird_dir.iterdir()) == []
    assert f"Removed from favorites on player 3: {video}" in caplog.text
    # The demotion and the condemnation look the same on screen otherwise, so
    # each says which one it was.
    assert [(op.op, op.key, op.source) for op in ops] == [
        ("notice", "Unfavorited", "landscape")
    ]


def test_demoting_a_favorite_the_player_already_left_does_not_drag_it_back(tmp_path: Path):
    """Back-dating exists so a condemned clip is the one dropped.  A demotion
    drops nothing, so a satellite that has moved on is left where it is."""
    config = _make_config(tmp_path)
    video = _favorite_clip(tmp_path, config, "spoken_about.mp4")
    _set_current(config, 3, str(tmp_path / "clips" / "advanced_to.mp4"))

    _state, ops = _discard(3, _make_state(locked3=False), config, target_path=str(video))

    assert _cmds(config, 3) == []
    assert str(video) not in config.favs_file.read_text(encoding="utf-8")
    assert [op.key for op in ops] == ["Unfavorited"]


def test_discard_with_no_clip_on_screen_announces_nothing(tmp_path: Path):
    """Nothing to unfavorite and nothing to condemn, so nothing is claimed."""
    config = _make_config(tmp_path)
    _set_current(config, 2, "")

    _state, ops = _discard(2, _make_state(locked2=False), config)

    assert ops == []


def test_discarding_a_demoted_clip_again_marks_it_weird(tmp_path: Path):
    """The demotion is one step only — once a clip is out of the favorites, the
    next discard is the full condemnation: dropped from the playlist (TRASH) and
    moved out of the library."""
    config = _make_config(tmp_path)
    video = _favorite_clip(tmp_path, config, "twice.mp4")
    _set_current(config, 2, str(video))

    _state, demote_ops = _discard(2, _make_state(locked2=False), config)
    assert video.exists()

    _state, condemn_ops = _discard(2, _make_state(locked2=False), config)

    assert [op.key for op in demote_ops] == ["Unfavorited"]
    assert [op.key for op in condemn_ops] == ["Marked weird"]
    # And in different colors: undoing a favoriting is one of the things green
    # is kept for, condemning a clip that was never a favorite is not.
    assert [op.level for op in demote_ops] == [FAVORITE_NOTICE_LEVEL]
    assert [op.level for op in condemn_ops] == [NOTICE]
    assert _cmds(config, 2) == ["NEXT", "TRASH"]
    assert not video.exists()
    assert [p.name for p in config.weird_dir.iterdir()] == ["twice.mp4"]
