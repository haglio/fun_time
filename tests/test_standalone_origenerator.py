from __future__ import annotations

import json
import os

import pytest

from fun_time.standalone_origenerator import (
    OpenOrigenerator,
    claim_the_osr2,
    take_it_over,
    the_open_origenerator,
)
from fun_time.win32_process import get_process_creation_time


def _offer(checkout, offer):
    (checkout / "state").mkdir(exist_ok=True)
    (checkout / "state" / "fun_time_offer.txt").write_text(offer, encoding="utf-8")


def _this_process():
    return f"{os.getpid()} {get_process_creation_time(os.getpid())}"


@pytest.mark.parametrize("offer", ["", "1234", "1234 soon", "one two"])
def test_an_offer_that_names_no_process_is_no_open_app(tmp_path, offer):
    _offer(tmp_path, offer)

    assert the_open_origenerator(tmp_path) is None


def test_an_open_app_is_the_process_its_offer_names(tmp_path):
    _offer(tmp_path, _this_process())

    assert the_open_origenerator(tmp_path) == OpenOrigenerator(
        os.getpid(), starting=False, checkout=tmp_path)


def test_an_app_still_starting_says_so_in_its_offer(tmp_path):
    _offer(tmp_path, f"{_this_process()} starting")

    assert the_open_origenerator(tmp_path) == OpenOrigenerator(
        os.getpid(), starting=True, checkout=tmp_path)


def test_an_offer_with_a_word_this_session_does_not_know_is_no_open_app(tmp_path):
    _offer(tmp_path, f"{_this_process()} sleeping")

    assert the_open_origenerator(tmp_path) is None


def test_a_takeover_lands_whole_under_its_own_name(tmp_path):
    (tmp_path / "state").mkdir()

    take_it_over(tmp_path, pid=4321, args=["--fun-time", "--x", "0"])

    assert [p.name for p in (tmp_path / "state").iterdir()] == ["fun_time_takeover.json"]
    assert json.loads((tmp_path / "state" / "fun_time_takeover.json").read_text(
        encoding="utf-8")) == {"pid": 4321, "args": ["--fun-time", "--x", "0"]}


def test_a_session_hosting_a_worktree_finds_the_everyday_copy_on_its_library(tmp_path):
    """One copy runs on a library at a time, so a session hosting a worktree
    takes over the everyday copy rather than opening a second (his call,
    2026-09-25, in origenerator's CLAUDE.md)."""
    everyday = tmp_path / "origenerator"
    worktree = everyday / ".claude" / "worktrees" / "my-branch"
    worktree.mkdir(parents=True)
    _offer(everyday, _this_process())

    assert the_open_origenerator(worktree) == OpenOrigenerator(
        os.getpid(), starting=False, checkout=everyday)


def _claim_in(checkout):
    return (checkout / "state" / "fun_time_session.txt").read_text(encoding="utf-8")


def test_a_session_claims_the_osr2_by_its_own_process(tmp_path):
    claim_the_osr2(tmp_path)

    assert _claim_in(tmp_path) == _this_process()


def test_a_session_hosting_a_worktree_claims_the_osr2_in_its_primary_checkout_too(tmp_path):
    primary = tmp_path / "origenerator"
    worktree = primary / ".claude" / "worktrees" / "my-branch"

    claim_the_osr2(worktree)

    assert (_claim_in(worktree), _claim_in(primary)) == (_this_process(), _this_process())
