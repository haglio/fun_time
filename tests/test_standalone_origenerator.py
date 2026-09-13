from __future__ import annotations

import json

import pytest

from fun_time.standalone_origenerator import take_it_over, the_open_origenerator


@pytest.mark.parametrize("offer", ["", "1234", "1234 soon", "one two"])
def test_an_offer_that_names_no_process_is_no_open_app(tmp_path, offer):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "fun_time_offer.txt").write_text(offer, encoding="utf-8")

    assert the_open_origenerator(tmp_path) == 0


def test_a_takeover_lands_whole_under_its_own_name(tmp_path):
    (tmp_path / "state").mkdir()

    take_it_over(tmp_path, pid=4321, args=["--fun-time", "--x", "0"])

    assert [p.name for p in (tmp_path / "state").iterdir()] == ["fun_time_takeover.json"]
    assert json.loads((tmp_path / "state" / "fun_time_takeover.json").read_text(
        encoding="utf-8")) == {"pid": 4321, "args": ["--fun-time", "--x", "0"]}
