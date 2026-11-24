from __future__ import annotations

from types import SimpleNamespace

import pytest

from fun_time import ending_with_fun_time
from fun_time.ending_with_fun_time import free_from_fun_time, tie_to_fun_time


class _Job:
    def __init__(self) -> None:
        self.freed = False

    def let_its_processes_outlive_it(self) -> None:
        self.freed = True


@pytest.fixture
def jobs(monkeypatch) -> dict[int, _Job]:
    made: dict[int, _Job] = {}

    def tie(part):
        made[part.pid] = _Job()
        return made[part.pid]

    monkeypatch.setattr(ending_with_fun_time, "tie_to_this_process", tie)
    monkeypatch.setattr(ending_with_fun_time, "_TIES", {})
    return made


def test_a_part_tied_to_fun_time_comes_back_as_its_pid(jobs):
    assert tie_to_fun_time(SimpleNamespace(pid=4321)) == 4321
    assert 4321 in jobs


def test_a_part_freed_from_fun_time_is_let_outlive_it(jobs):
    tie_to_fun_time(SimpleNamespace(pid=4321))

    free_from_fun_time(4321)

    assert jobs[4321].freed


def test_freeing_a_part_that_was_never_tied_does_nothing(jobs):
    free_from_fun_time(4321)

    assert jobs == {}


def test_a_part_windows_would_not_tie_is_still_started(monkeypatch):
    monkeypatch.setattr(ending_with_fun_time, "tie_to_this_process", lambda part: None)
    monkeypatch.setattr(ending_with_fun_time, "_TIES", {})

    assert tie_to_fun_time(SimpleNamespace(pid=4321)) == 4321
    free_from_fun_time(4321)


def test_a_job_windows_will_not_free_leaves_the_part_tied_and_says_so(jobs, caplog):
    tie_to_fun_time(SimpleNamespace(pid=4321))

    def refused() -> None:
        raise OSError("SetInformationJobObject failed")

    jobs[4321].let_its_processes_outlive_it = refused

    free_from_fun_time(4321)

    assert "4321" in caplog.text
    assert "SetInformationJobObject failed" in caplog.text
