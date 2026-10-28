from __future__ import annotations

import pytest
from app_support import origenerator_handoff as handoff

from fun_time.room_funestras import RoomFunestra
from fun_time.standalone_origenerator import (
    OpenOrigenerator,
    claim_the_osr2,
    the_open_origenerator,
)


def _offer(checkout, *, starting=False):
    handoff.offer_this_process(checkout / "state", starting=starting)


def _mark_a_slideshow(checkout, shape):
    mark = handoff.slideshow_mark_of(handoff.this_process(), shape)
    (checkout / "state" / handoff.SHOWING).write_text(mark, encoding="utf-8")


def test_no_offer_is_no_open_app(tmp_path):
    assert the_open_origenerator(tmp_path) is None


def test_an_open_app_is_the_process_its_offer_names(tmp_path):
    _offer(tmp_path)

    assert the_open_origenerator(tmp_path) == OpenOrigenerator(
        handoff.this_process().pid, starting=False, place=tmp_path / "state", slideshow_on=())


def test_an_app_still_starting_says_so_in_its_offer(tmp_path):
    _offer(tmp_path, starting=True)

    assert the_open_origenerator(tmp_path).starting


def test_an_open_app_is_taken_over_through_the_checkout_its_offer_stood_in(tmp_path):
    _offer(tmp_path)

    assert the_open_origenerator(tmp_path).checkout == tmp_path


@pytest.mark.parametrize(("shape", "satellites"), [
    ("portrait", (RoomFunestra.PORTRAIT,)),
    ("landscape", (RoomFunestra.LANDSCAPE,)),
    ("both", RoomFunestra.SATELLITES)])
def test_an_open_app_showing_a_slideshow_names_the_satellites_of_its_shape(
        tmp_path, shape, satellites):
    _offer(tmp_path)
    _mark_a_slideshow(tmp_path, shape)

    assert the_open_origenerator(tmp_path).slideshow_on == satellites


def test_a_session_hosting_a_worktree_finds_the_everyday_copy_on_its_library(tmp_path):
    """One copy runs on a library at a time, so a session hosting a worktree
    takes over the everyday copy rather than opening a second (his call,
    2026-09-25, in origenerator's CLAUDE.md)."""
    everyday = tmp_path / "origenerator"
    worktree = everyday / ".claude" / "worktrees" / "my-branch"
    worktree.mkdir(parents=True)
    _offer(everyday)

    assert the_open_origenerator(worktree) == OpenOrigenerator(
        handoff.this_process().pid, starting=False, place=everyday / "state", slideshow_on=())


def test_a_session_hosting_a_worktree_claims_the_osr2_in_its_primary_checkout_too(tmp_path):
    primary = tmp_path / "origenerator"
    worktree = primary / ".claude" / "worktrees" / "my-branch"

    claim_the_osr2(worktree)

    assert [handoff.a_session_claims_the_osr2(checkout / "state")
            for checkout in (worktree, primary)] == [True, True]
