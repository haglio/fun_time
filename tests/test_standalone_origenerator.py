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

    assert the_open_origenerator(tmp_path) == OpenOrigenerator(os.getpid(), starting=False)


def test_an_app_still_starting_says_so_in_its_offer(tmp_path):
    _offer(tmp_path, f"{_this_process()} starting")

    assert the_open_origenerator(tmp_path) == OpenOrigenerator(os.getpid(), starting=True)


def test_an_offer_with_a_word_this_session_does_not_know_is_no_open_app(tmp_path):
    _offer(tmp_path, f"{_this_process()} sleeping")

    assert the_open_origenerator(tmp_path) is None


def test_a_takeover_lands_whole_under_its_own_name(tmp_path):
    (tmp_path / "state").mkdir()

    take_it_over(tmp_path, pid=4321, args=["--fun-time", "--x", "0"])

    assert [p.name for p in (tmp_path / "state").iterdir()] == ["fun_time_takeover.json"]
    assert json.loads((tmp_path / "state" / "fun_time_takeover.json").read_text(
        encoding="utf-8")) == {"pid": 4321, "args": ["--fun-time", "--x", "0"]}


def test_a_session_claims_the_osr2_by_its_own_process(tmp_path):
    claim_the_osr2(tmp_path)

    assert (tmp_path / "state" / "fun_time_session.txt").read_text(
        encoding="utf-8").split() == [
            str(os.getpid()), str(get_process_creation_time(os.getpid()))]
