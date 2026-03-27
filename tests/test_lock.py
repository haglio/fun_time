from __future__ import annotations

from fun_time.lock import build_lock_plan


def test_toggle_lock_locks_and_favorites_when_unlocked():
    plan = build_lock_plan("toggle-lock", which=2, locked=False, current_path="clip.mp4")

    assert plan.next_locked is True
    assert plan.repeat_mode == "one"
    assert plan.ensure_in_favs is True
    assert plan.advance_playlist is False
    assert plan.log_message == "Locked portrait VLC"


def test_toggle_lock_unlocks_and_advances_when_locked():
    plan = build_lock_plan("toggle-lock", which=3, locked=True, current_path="clip.mp4")

    assert plan.next_locked is False
    assert plan.repeat_mode == "all"
    assert plan.ensure_in_favs is False
    assert plan.advance_playlist is True
    assert plan.log_message == "Unlocked landscape VLC"


def test_cancel_lock_only_changes_state_when_currently_locked():
    locked_plan = build_lock_plan("cancel-lock", which=2, locked=True, current_path="")
    unlocked_plan = build_lock_plan("cancel-lock", which=2, locked=False, current_path="")

    assert locked_plan.next_locked is False
    assert locked_plan.repeat_mode == "all"
    assert unlocked_plan.next_locked is False
    assert unlocked_plan.repeat_mode == ""


def test_discard_unlocks_removes_from_favs_advances_and_moves_to_weird():
    plan = build_lock_plan("discard", which=3, locked=True, current_path="odd.mp4")

    assert plan.next_locked is False
    assert plan.repeat_mode == "all"
    assert plan.remove_from_favs is True
    assert plan.advance_playlist is True
    assert plan.move_to_weird is True
    assert plan.log_message == "Discarding from player 3: odd.mp4"
