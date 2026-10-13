from __future__ import annotations

import json
from pathlib import Path

import pytest

from fun_time.hosted_contract import CONTRACT_FILE, declared_flags, the_args_it_declares
from fun_time.hosted_origenerator import HAND_OVER, TAKE_BACK
from fun_time.room_funestras import RoomFunestra
from fun_time.window_layout import WindowLayoutPlan, WindowRect
from fun_time.window_roles import ORIGENERATOR_TITLE
from fun_time.windows_bridge_startup import HandedFunestra, origenerator_session_args
from fun_time_vr.gallery_panel import HOSTED_RELEASE, HOVER, SCROLL
from fun_time_vr.pointer import DRAG, PRESS, RIGHT_CLICK
from tests.origenerator_contract import answers, published


def _the_flags_fun_time_sends(**headset) -> set[str]:
    a_rect = WindowRect(0, 0, 10, 10)
    args = origenerator_session_args(
        layout_plan=WindowLayoutPlan(portrait=a_rect, landscape=a_rect, dashboard=a_rect,
                                     random_favs_browser=a_rect),
        command_file="c", paused_file="p", status_file="s", dashboard_cmd_file="d",
        funestras={funestra.label: HandedFunestra("playlist", "cmd", "status", "hud")
                   for funestra in RoomFunestra.SATELLITES},
        **headset)
    return {word for word in args if word.startswith("--")}


def test_every_flag_fun_time_sends_is_one_the_shipped_origenerator_declares():
    in_a_headset = _the_flags_fun_time_sends(frames_file="f.bin", input_file="i.txt")

    assert in_a_headset <= declared_flags(published())


def test_every_flag_the_shipped_origenerator_requires_is_one_fun_time_sends():
    assert set(published()["required_flags"]) <= _the_flags_fun_time_sends()


def test_the_words_fun_time_sends_through_the_pair_are_the_shipped_origenerator_s():
    assert set(published()["headset_words"]) == {
        PRESS, HOSTED_RELEASE, DRAG, HOVER, SCROLL, RIGHT_CLICK}


def test_what_a_session_tells_a_kept_copy_is_a_line_the_shipped_origenerator_answers():
    assert answers(TAKE_BACK)
    assert answers(f"{HAND_OVER}|C:/state/origenerator_frame.bin|C:/state/origenerator_input.txt")


def test_the_caption_fun_time_finds_it_by_is_the_one_the_shipped_origenerator_wears():
    assert published()["window_title"] == ORIGENERATOR_TITLE


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
