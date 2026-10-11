from __future__ import annotations

from tests import child_reports
from tests.child_reports import StartedProcess, with_what_it_started


def test_a_process_older_than_its_parent_was_started_by_an_earlier_owner_of_the_parents_pid(
        monkeypatch):
    created = {756: 200, 764: 100, 900: 300}
    monkeypatch.setattr(child_reports, "list_child_pids", lambda pid: [764, 900] if pid == 756 else [])
    monkeypatch.setattr(child_reports, "get_process_creation_time", created.get)

    assert with_what_it_started(756) == [StartedProcess(756, 200), StartedProcess(900, 300)]
