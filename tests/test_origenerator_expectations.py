from __future__ import annotations

import json
import os

import pytest

from fun_time.hosted_contract import SAID_AT_FIELD, declared_flags
from fun_time.room_funestras import RoomFunestra
from fun_time.windows_bridge_dispatch_loop import REQUEST_NEWS
from tests.origenerator_contract import answers, is_read_by, published
from tests.origenerator_expectations import EXPECTATIONS, LEFT_UNANSWERED, published_text

REWRITE = "FUN_TIME_REWRITE_ORIGENERATOR_EXPECTATIONS"


def test_the_published_expectations_are_what_this_code_relies_on(tmp_path):
    relied_on = published_text(tmp_path)
    if os.environ.get(REWRITE):
        EXPECTATIONS.write_text(relied_on, encoding="utf-8", newline="\n")

    assert EXPECTATIONS.read_text(encoding="utf-8") == relied_on, (
        f"{EXPECTATIONS.name} is not what this code relies on: rerun this test "
        f"with {REWRITE}=1 to rewrite it")


@pytest.fixture(scope="module")
def relied_on() -> dict:
    return json.loads(EXPECTATIONS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def shipped() -> dict:
    return published()


def test_the_shipped_origenerator_is_the_module_run_and_wears_the_caption_found(
        relied_on, shipped):
    assert (shipped["module"], shipped["window_title"]) == (
        relied_on["module"], relied_on["window_title"])


def test_the_shipped_origenerator_declares_every_flag_of_every_launch(relied_on, shipped):
    undeclared = {launch: sorted(set(flags) - declared_flags(shipped))
                  for launch, flags in relied_on["launches"].items()}

    assert not any(undeclared.values()), undeclared


def test_every_launch_carries_every_flag_the_shipped_origenerator_requires(relied_on, shipped):
    missing = {launch: sorted(set(shipped["required_flags"]) - set(flags))
               for launch, flags in relied_on["launches"].items()}

    assert not any(missing.values()), missing


def test_a_shipped_origenerator_taking_when_a_line_was_said_takes_it_in_the_field_filled(
        relied_on, shipped):
    suffix = shipped.get("said_at_suffix", "")

    assert relied_on["said_at_field"] == SAID_AT_FIELD
    assert not suffix or "{" + SAID_AT_FIELD + "}" in suffix


def test_the_shipped_origenerator_reads_every_word_a_headset_writes(relied_on, shipped):
    assert set(relied_on["headset_words"]) <= set(shipped["headset_words"])


def test_the_shipped_origenerator_answers_every_line_a_room_writes(relied_on, shipped):
    assert [line for line in relied_on["command_lines"] if not answers(line, shipped)] == []


def test_the_room_answers_every_line_the_shipped_origenerator_tells_it_of_a_request(shipped):
    assert set(shipped["request_lines"]) <= set(REQUEST_NEWS)


def test_the_lines_left_unanswered_are_lines_the_shipped_origenerator_refuses(shipped):
    answered = [line for line in (f"{funestra.label}_{said}" for said in LEFT_UNANSWERED
                                  for funestra in RoomFunestra.SATELLITES)
                if answers(line, shipped)]

    assert answered == []


def test_every_line_left_unanswered_says_why():
    assert [said for said, why in LEFT_UNANSWERED.items() if not why.strip()] == []


def test_the_shipped_origenerator_names_each_file_the_two_apps_hand_each_other_alike(
        relied_on, shipped):
    assert {side: entry["file"] for side, entry in shipped["handoff"].items()} == {
        side: entry["file"] for side, entry in relied_on["handoff"].items()}


def test_what_either_app_writes_in_a_handoff_file_is_what_the_other_reads(relied_on, shipped):
    unread = [(side, written)
              for writer, reader in ((relied_on, shipped), (shipped, relied_on))
              for side, entry in writer["handoff"].items()
              for written in entry.get("writes", ())
              if not is_read_by(written, reader["handoff"][side]["reads"])]

    assert unread == []
