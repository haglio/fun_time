from __future__ import annotations

import pytest
from app_support import origenerator_handoff as handoff

from fun_time.standalone_origenerator import (
    OpenOrigenerator,
    claim_the_osr2,
    the_open_origenerator,
)
from tests.git_checkouts import a_worktree_of


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
        handoff.this_process().pid, starting=False, place=tmp_path / "state", showing_a_slideshow=False)


def test_an_app_still_starting_says_so_in_its_offer(tmp_path):
    _offer(tmp_path, starting=True)

    assert the_open_origenerator(tmp_path).starting


def test_an_open_app_is_taken_over_through_the_checkout_its_offer_stood_in(tmp_path):
    _offer(tmp_path)

    assert the_open_origenerator(tmp_path).checkout == tmp_path


@pytest.mark.parametrize("shape", ["portrait", "landscape", "both"])
def test_an_open_app_showing_a_slideshow_of_any_shape_says_so(tmp_path, shape):
    _offer(tmp_path)
    _mark_a_slideshow(tmp_path, shape)

    assert the_open_origenerator(tmp_path).showing_a_slideshow


def test_a_hosted_worktree_finds_the_one_copy_its_library_allows(tmp_path):
    everyday = tmp_path / "origenerator"
    worktree = a_worktree_of(everyday, everyday / ".claude" / "worktrees" / "my-branch")
    _offer(everyday)

    assert the_open_origenerator(worktree) == OpenOrigenerator(
        handoff.this_process().pid, starting=False, place=everyday / "state", showing_a_slideshow=False)


def test_a_session_hosting_a_worktree_claims_the_osr2_in_its_primary_checkout_too(tmp_path):
    primary = tmp_path / "origenerator"
    worktree = a_worktree_of(primary, primary / ".claude" / "worktrees" / "my-branch")

    claim_the_osr2(worktree)

    assert [handoff.a_session_claims_the_osr2(checkout / "state")
            for checkout in (worktree, primary)] == [True, True]
