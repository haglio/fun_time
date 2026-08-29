"""fun_time/lock.py directly: the pure decision table every lock verb follows.

The observable half — which verbs reach the satellite's command file, what
lands in the favorites and the weird dir — is pinned in test_command_dispatch.
These pin the table itself at its own level, one field per decision, which is
where a wrong entry is cheapest to see.
"""
from __future__ import annotations

import pytest

from fun_time.lock import build_lock_plan


def test_locking_favorites_the_clip_and_opens_its_tab():
    plan = build_lock_plan("toggle-lock", which=2, locked=False, current_path="C:/v/c.mp4")

    assert plan.next_locked is True
    assert plan.ensure_in_favs is True
    assert plan.open_rfb_tab is True
    assert not plan.advance_playlist and not plan.remove_from_favs
    assert not plan.move_to_weird and not plan.drop_from_playlist


def test_locking_with_no_clip_on_screen_favorites_nothing():
    plan = build_lock_plan("toggle-lock", which=2, locked=False, current_path="")

    assert plan.next_locked is True
    assert plan.ensure_in_favs is False


def test_unlocking_releases_the_hold_and_moves_on():
    plan = build_lock_plan("toggle-lock", which=3, locked=True, current_path="C:/v/c.mp4")

    assert plan.next_locked is False
    assert plan.advance_playlist is True
    assert plan.open_rfb_tab is False
    assert "landscape" in plan.log_message


def test_discarding_a_favorite_is_a_demotion_not_a_condemnation():
    plan = build_lock_plan("discard", which=3, locked=False,
                           current_path="C:/v/kept.mp4", is_favorite=True)

    assert plan.remove_from_favs is True
    assert plan.advance_playlist is True
    # The clip stays in the library and in the playlist: only the favoriting
    # is undone, so stepping back lands on it again.
    assert plan.move_to_weird is False
    assert plan.drop_from_playlist is False
    assert plan.notice_message == "Unfavorited"
    assert plan.notice_about_favorites is True


def test_discarding_a_non_favorite_condemns_it():
    plan = build_lock_plan("discard", which=2, locked=False,
                           current_path="C:/v/odd.mp4", is_favorite=False)

    assert plan.remove_from_favs is True
    assert plan.move_to_weird is True
    assert plan.drop_from_playlist is True
    assert plan.notice_message == "Marked weird"
    assert plan.notice_about_favorites is False


def test_discarding_nothing_touches_nothing_and_claims_nothing():
    plan = build_lock_plan("discard", which=2, locked=False, current_path="")

    assert plan.remove_from_favs is False
    assert plan.move_to_weird is False
    assert plan.notice_message == ""


def test_an_unknown_action_is_refused_outright():
    with pytest.raises(ValueError):
        build_lock_plan("bless", which=2, locked=False, current_path="C:/v/c.mp4")
