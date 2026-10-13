import os
import time
from datetime import datetime, timedelta

import pytest
from app_support.integration_runs import RunRecord, read_run
from app_support.win32 import process_started

from tests.integration.line_record import (
    RUN_RECORD_ENV,
    Visit,
    require_a_run_that_holds_the_machine,
)

JOINED = datetime(2026, 10, 11, 13, 0).astimezone()


def _join(folder, **changes) -> Visit:
    asked = {"checkout": "crown-colors", "branch": "claude/crown-colors", "session": "5e55-10n",
             "what": ["-k", "test_crown"], "short": True, "now": JOINED}
    return Visit.join(folder, **{**asked, **changes})


def test_a_run_writes_down_when_it_joined_the_line_got_the_machine_and_how_it_ended(tmp_path):
    visit = _join(tmp_path)
    visit.got_the_machine(now=JOINED + timedelta(minutes=40))

    assert visit.ended_with(1, now=JOINED + timedelta(minutes=55)) == 1
    visit.leave()
    assert read_run(visit.path) == RunRecord(
        checkout="crown-colors", branch="claude/crown-colors", what=["-k", "test_crown"], short=True, pid=os.getpid(),
        joined=JOINED, session="5e55-10n", process_started=process_started(os.getpid()),
        got_the_machine=JOINED + timedelta(minutes=40), ended=JOINED + timedelta(minutes=55), exit_code=1,
        seen=JOINED + timedelta(minutes=55))


def test_a_run_that_leaves_without_saying_how_it_ended_is_written_down_as_ended_without_a_result(tmp_path):
    visit = _join(tmp_path)
    visit.got_the_machine(now=JOINED + timedelta(minutes=1))

    visit.leave(now=JOINED + timedelta(minutes=2))

    written = read_run(visit.path)
    assert (written.ended, written.exit_code, written.seen) == (JOINED + timedelta(minutes=2), None,
                                                                JOINED + timedelta(minutes=2))


def test_a_run_writes_down_that_it_is_still_there_every_little_while_until_it_leaves(tmp_path):
    visit = _join(tmp_path, seen_every_s=0.01)
    try:
        deadline = time.monotonic() + 10
        while read_run(visit.path).seen == JOINED and time.monotonic() < deadline:
            time.sleep(0.01)
        assert read_run(visit.path).seen > JOINED
    finally:
        visit.leave(now=JOINED + timedelta(minutes=5))

    last = read_run(visit.path)
    time.sleep(0.05)
    assert read_run(visit.path) == last


def test_tests_start_only_inside_a_run_that_wrote_itself_down_and_holds_the_machine(tmp_path):
    visit = _join(tmp_path)
    told = {RUN_RECORD_ENV: str(visit.path)}
    try:
        with pytest.raises(RuntimeError):
            require_a_run_that_holds_the_machine(told)
        visit.got_the_machine(now=JOINED + timedelta(minutes=1))
        require_a_run_that_holds_the_machine(told)
        visit.ended_with(0, now=JOINED + timedelta(minutes=2))
        with pytest.raises(RuntimeError):
            require_a_run_that_holds_the_machine(told)
    finally:
        visit.leave()


def test_tests_started_outside_any_run_are_refused_and_told_how_to_start_one(tmp_path):
    for environment in ({}, {RUN_RECORD_ENV: str(tmp_path / "no-such-run.json")}):
        with pytest.raises(RuntimeError) as refusal:
            require_a_run_that_holds_the_machine(environment)

        assert "-m tests.integration.hidden_desktop" in str(refusal.value)


def test_joining_the_line_forgets_runs_older_than_two_weeks(tmp_path):
    old, recent = tmp_path / "old.json", tmp_path / "recent.json"
    for path, age in ((old, timedelta(days=15)), (recent, timedelta(days=13))):
        path.write_text("{}", encoding="utf-8")
        os.utime(path, ((JOINED - age).timestamp(), (JOINED - age).timestamp()))

    _join(tmp_path, session="", what=[], short=False).leave()

    assert not old.exists() and recent.exists()
