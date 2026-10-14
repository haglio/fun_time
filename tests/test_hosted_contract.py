from __future__ import annotations

import json
from pathlib import Path

import pytest

from fun_time.hosted_contract import CONTRACT_FILE, the_args_it_declares


def _a_checkout_declaring(tmp_path: Path, **groups) -> Path:
    contract = {"required_flags": [], "region_flags": {}, "player_flags": {},
                "headset_flags": [], **groups}
    (tmp_path / CONTRACT_FILE).write_text(json.dumps(contract), encoding="utf-8")
    return tmp_path


def test_a_flag_the_checkout_does_not_declare_is_left_off_with_its_value(tmp_path):
    checkout = _a_checkout_declaring(tmp_path, required_flags=["--fun-time", "--x"])

    assert the_args_it_declares(checkout, ["--fun-time", "--x", "1", "--frames-file", "f.bin"]) == [
        "--fun-time", "--x", "1"]


@pytest.mark.parametrize("group, flag", [
    ("region_flags", "--portrait-x"),
    ("player_flags", "--landscape-playlist"),
    ("headset_flags", "--frames-file"),
])
def test_a_flag_it_declares_in_any_group_is_sent(tmp_path, group, flag):
    declared = [flag] if group == "headset_flags" else {"a side": [flag]}
    checkout = _a_checkout_declaring(tmp_path, **{group: declared})

    assert the_args_it_declares(checkout, [flag, "7"]) == [flag, "7"]


def test_what_was_left_off_is_logged_with_the_checkout_that_did_not_declare_it(tmp_path, caplog):
    checkout = _a_checkout_declaring(tmp_path, required_flags=["--fun-time"])

    with caplog.at_level("WARNING", logger="fun_time.hosted_contract"):
        the_args_it_declares(checkout, ["--fun-time", "--frames-file", "f.bin", "--input-file", "i"])

    assert "--frames-file, --input-file" in caplog.text
    assert str(checkout) in caplog.text


def test_a_checkout_that_publishes_no_contract_is_sent_every_flag(tmp_path):
    assert the_args_it_declares(tmp_path, ["--fun-time", "--frames-file", "f.bin"]) == [
        "--fun-time", "--frames-file", "f.bin"]


def test_a_contract_from_before_the_headset_declares_none_of_its_flags(tmp_path):
    (tmp_path / CONTRACT_FILE).write_text(json.dumps({
        "required_flags": ["--fun-time"], "region_flags": {}, "player_flags": {}}),
        encoding="utf-8")

    assert the_args_it_declares(tmp_path, ["--fun-time", "--frames-file", "f.bin"]) == ["--fun-time"]
