from __future__ import annotations

from fun_time import funestra_deaths
from fun_time.funestra_deaths import the_part_that_closed
from fun_time.windows_bridge_orchestrator import ChildProcess, _wait_for_the_session_to_end


def _app(**up):
    return {key: ChildProcess(pid=pid, created_at=pid * 100) for key, pid in up.items()}


def _born(*living: int):
    return {pid: pid * 100 for pid in living}.get


def test_an_app_with_every_part_up_has_none_closed():
    app = _app(portrait_pid=30, origenerator_pid=40)

    assert the_part_that_closed(app, created_at=_born(30, 40)) is None


def test_the_part_that_closed_is_named_as_he_knows_it():
    app = _app(portrait_pid=30, origenerator_pid=40)

    assert the_part_that_closed(app, created_at=_born(30)) == "Origenerator Core"


def test_a_pid_windows_has_handed_to_another_program_is_a_part_that_closed():
    app = _app(audio_pid=60)

    assert the_part_that_closed(app, created_at={60: 1}.get) == "the audio companion"


def test_a_part_the_session_never_launched_never_closes():
    app = {"origenerator_pid": ChildProcess(pid=0, created_at=0)}

    assert the_part_that_closed(app, created_at=_born()) is None


class _StillRunning:
    def poll(self):
        return None

    def wait(self):
        return 0


def test_the_session_ends_when_a_part_of_its_app_closes(tmp_path, monkeypatch):
    monkeypatch.setattr(funestra_deaths, "process_started", _born(30))

    ended = _wait_for_the_session_to_end(
        _StillRunning(), tmp_path, children=_app(portrait_pid=30, origenerator_pid=40),
        poll_s=0)

    assert ended.closed == "Origenerator Core"
    assert ended.exit_code != 0
