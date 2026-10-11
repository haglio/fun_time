import json
import os
from datetime import datetime, timedelta

from tests.integration.line_record import Visit

JOINED = datetime(2026, 10, 11, 13, 0).astimezone()


def test_a_run_writes_when_it_joined_the_line_got_the_machine_and_how_it_ended(tmp_path):
    visit = Visit.join(tmp_path, checkout="crown-colors", branch="claude/crown-colors", what=["-k", "test_crown"],
                       short=True, now=JOINED)
    visit.got_the_machine(now=JOINED + timedelta(minutes=40))

    assert visit.ended_with(1, now=JOINED + timedelta(minutes=55)) == 1
    [written] = tmp_path.glob("*.json")
    assert json.loads(written.read_text(encoding="utf-8")) == {
        "checkout": "crown-colors", "branch": "claude/crown-colors", "what": ["-k", "test_crown"], "short": True,
        "pid": os.getpid(), "joined": JOINED.isoformat(timespec="seconds"),
        "got_the_machine": (JOINED + timedelta(minutes=40)).isoformat(timespec="seconds"),
        "ended": (JOINED + timedelta(minutes=55)).isoformat(timespec="seconds"), "exit_code": 1}


def test_joining_the_line_forgets_runs_older_than_two_weeks(tmp_path):
    old, recent = tmp_path / "old.json", tmp_path / "recent.json"
    for path, age in ((old, timedelta(days=15)), (recent, timedelta(days=13))):
        path.write_text("{}", encoding="utf-8")
        os.utime(path, ((JOINED - age).timestamp(), (JOINED - age).timestamp()))

    Visit.join(tmp_path, checkout="crown-colors", branch="claude/crown-colors", what=[], short=False, now=JOINED)

    assert not old.exists() and recent.exists()
