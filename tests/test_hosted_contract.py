from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from fun_time.hosted_contract import (
    CONTRACT_FILE,
    HostedVocabulary,
    the_args_it_declares,
    what_it_answers_by_voice,
)


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


def _answering(stdout: str, asked: list):
    def run(argv, **kwargs):
        asked.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
    return run


def test_the_app_is_asked_what_it_answers_by_voice_through_the_module_its_contract_names(
        tmp_path):
    checkout = _a_checkout_declaring(tmp_path, spoken_phrases_module="fabricated.voice")
    asked: list = []
    answer = json.dumps({"phrases": ["fix alpha", "request"], "openers": ["request"],
                         "speech_hint": "Fabricated hint."})

    heard = what_it_answers_by_voice(checkout, python_exe="fabricated-python.exe",
                                     launch_kwargs={"cwd": str(checkout)},
                                     run=_answering(answer, asked))

    assert heard == HostedVocabulary(frozenset({"fix alpha", "request"}),
                                     frozenset({"request"}), "Fabricated hint.")
    assert [(argv, kwargs["cwd"]) for argv, kwargs in asked] == [
        (["fabricated-python.exe", "-m", "fabricated.voice"], str(checkout))]


def test_an_app_whose_contract_names_no_such_module_is_heard_saying_nothing(tmp_path, caplog):
    checkout = _a_checkout_declaring(tmp_path)
    asked: list = []

    with caplog.at_level("WARNING", logger="fun_time.hosted_contract"):
        heard = what_it_answers_by_voice(checkout, python_exe="fabricated-python.exe",
                                         launch_kwargs={}, run=_answering("", asked))

    assert heard == HostedVocabulary()
    assert asked == []
    assert str(checkout) in caplog.text


def _failing_with(error: Exception):
    def run(argv, **kwargs):
        raise error
    return run


@pytest.mark.parametrize("run", [
    _answering("not an answer", []),
    _answering(json.dumps({"phrases": ["fix alpha"]}), []),
    _failing_with(subprocess.TimeoutExpired(["python"], 30)),
    _failing_with(subprocess.CalledProcessError(1, ["python"])),
    _failing_with(OSError("no such interpreter")),
])
def test_an_app_that_cannot_answer_is_heard_saying_nothing(tmp_path, caplog, run):
    checkout = _a_checkout_declaring(tmp_path, spoken_phrases_module="fabricated.voice")

    with caplog.at_level("WARNING", logger="fun_time.hosted_contract"):
        heard = what_it_answers_by_voice(checkout, python_exe="fabricated-python.exe",
                                         launch_kwargs={}, run=run)

    assert heard == HostedVocabulary()
    assert str(checkout) in caplog.text
