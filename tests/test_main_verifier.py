from __future__ import annotations

import json
import os
import sqlite3
import sys
import tomllib
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import coverage
import pytest

from fun_time.win32_process import is_process_alive
from tests.integration import commands, fix_sessions, main_verifier
from tests.integration.coverage_map import CoverageMap


def test_the_change_that_broke_main_is_the_first_one_the_failing_tests_fail_on():
    commits = ["a1", "b2", "c3", "d4", "e5", "f6"]
    asked = []

    def fails_at(commit):
        asked.append(commit)
        return commits.index(commit) >= 3

    assert main_verifier.first_bad(commits, fails_at) == "d4"
    assert len(asked) <= 3


def test_main_is_run_once_per_head_it_has_not_yet_passed_or_failed(tmp_path):
    ledger = main_verifier.Ledger(tmp_path / "main_verifier.json")
    assert main_verifier.next_step(ledger, "c3") == "verify"

    ledger.passed("c3")
    assert main_verifier.next_step(ledger, "c3") == "nothing"

    ledger.failed("d4")
    assert main_verifier.next_step(ledger, "d4") == "wait"
    assert main_verifier.next_step(ledger, "e5") == "verify"


def test_what_main_passed_and_failed_survives_a_restart(tmp_path):
    path = tmp_path / "main_verifier.json"
    main_verifier.Ledger(path).passed("c3")
    main_verifier.Ledger(path).failed("d4")

    ledger = main_verifier.Ledger(path)

    assert (ledger.verified, ledger.failed_head) == ("c3", "d4")


WHOLE_RUN = Path("whole-run.txt")
REPO_PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


class FakeBench:
    def __init__(self, head, commits, broken_from=None, tests=("tests/integration/test_x.py::test_y",),
                 flaky=False, stale_map=False, restart_asked=False, reruns_failing=None, incidents=(),
                 sessions=None, pull_requests=None):
        self.head_commit, self.commits, self.broken_from = head, commits, broken_from
        self.tests, self.flaky, self.stale_map = list(tests), flaky, stale_map
        self.restart_asked = restart_asked
        self.reruns_failing = {commit: list(fails) for commit, fails in (reruns_failing or {}).items()}
        self.earlier, self.sessions = list(incidents), dict(sessions or {})
        self.pull_requests = dict(pull_requests or {})
        self.released, self.recorded, self.taken_back, self.measured = [], [], [], []
        self.fixing, self.events, self.shipped_again, self.stopped = [], [], [], []

    def head(self):
        return self.head_commit

    def commits_since(self, base, head):
        start = self.commits.index(base) + 1 if base in self.commits else 0
        return self.commits[start:self.commits.index(head) + 1]

    def run(self, commit, tests=()):
        self.events.append(("run", commit))
        broken = self.broken_from is not None and self.commits.index(commit) >= self.commits.index(self.broken_from)
        if self.flaky and tests:
            broken = False
        if tests and self.reruns_failing.get(commit):
            broken = self.reruns_failing[commit].pop(0)
        failed = self.tests if broken else []
        return main_verifier.RunResult(1 if broken else 0, failed, WHOLE_RUN)

    def release_to_production(self, commit):
        self.released.append(commit)

    def take_back(self, commit, among, tests):
        self.taken_back.append((commit, list(among)))
        return main_verifier.TakeBack(41, "Loop the row on the Main Funestra", 77)

    def pull_request_of(self, commit):
        return self.pull_requests.get(commit)

    def ship_again(self, pull_request, take_back, tests):
        self.shipped_again.append((pull_request, list(take_back)))
        return 79

    def start_fix_session(self, fix):
        self.fixing.append(fix)
        return "812181fb"

    def stop_fix_session(self, session):
        self.stopped.append(session)

    def install_for(self, commit):
        self.events.append(("install", commit))

    def record(self, incident):
        self.recorded.append(incident)

    def incidents(self):
        return self.earlier + self.recorded

    def fix_session_states(self):
        return self.sessions

    def coverage_map_is_stale(self):
        return self.stale_map

    def measure_coverage(self, commit):
        self.measured.append(commit)

    def take_restart_request(self):
        asked, self.restart_asked = self.restart_asked, False
        return asked


def never_naps(seconds):
    raise AssertionError("the runner went back to sleep")


def a_ledger(tmp_path, verified=None):
    ledger = main_verifier.Ledger(tmp_path / "main_verifier.json")
    if verified:
        ledger.passed(verified)
    return ledger


def test_a_head_that_passes_is_released_to_production(tmp_path):
    bench = FakeBench("c3", ["a1", "b2", "c3"])
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "passed"
    assert bench.released == ["c3"]
    assert ledger.verified == "c3"


def test_a_head_that_breaks_takes_the_change_back_and_starts_a_session_to_fix_it(tmp_path):
    bench = FakeBench("e5", ["a1", "b2", "c3", "d4", "e5"], broken_from="c3")
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "broke main"

    assert bench.taken_back == [("c3", ["b2", "c3", "d4", "e5"])]
    taken = main_verifier.TakeBack(41, "Loop the row on the Main Funestra", 77)
    assert bench.fixing == [main_verifier.fixing_a_take_back(taken, ["tests/integration/test_x.py::test_y"], WHOLE_RUN)]
    assert bench.released == []
    assert ledger.failed_head == "e5"
    assert bench.recorded == [{"kind": "broke main", "commit": "c3", "tests": ["tests/integration/test_x.py::test_y"],
                               "log": WHOLE_RUN, "taken_back_by": 77, "pull_request": 41,
                               "fix_session": "812181fb"}]


def test_a_fix_session_that_could_not_start_is_recorded_with_why(tmp_path):
    class UnsignedBench(FakeBench):
        def start_fix_session(self, fix):
            raise main_verifier.CommandFailed("the Claude command line is not signed in")

    bench = UnsignedBench("c3", ["a1", "b2", "c3"], broken_from="c3")

    assert main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench) == "broke main"

    assert bench.recorded[0]["taken_back_by"] == 77
    assert bench.recorded[0]["fix_session_not_started"] == "the Claude command line is not signed in"


def test_the_verifiers_own_installation_is_brought_up_to_a_head_before_it_is_run(tmp_path):
    bench = FakeBench("c3", ["a1", "b2", "c3"])

    main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench)

    assert bench.events[:2] == [("install", "c3"), ("run", "c3")]


def test_a_failure_that_passes_when_rerun_is_recorded_as_flaky_and_main_still_moves(tmp_path):
    bench = FakeBench("c3", ["a1", "b2", "c3"], broken_from="c3", flaky=True)
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "flaky"

    assert bench.recorded[0]["kind"] == "flaky"
    assert bench.released == ["c3"] and bench.taken_back == []


def test_a_run_that_fails_without_naming_a_test_waits_for_main_to_move_and_is_recorded(tmp_path):
    bench = FakeBench("c3", ["a1", "b2", "c3"], broken_from="c3", tests=())
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "broken run"

    assert ledger.failed_head == "c3"
    assert bench.recorded[0]["kind"] == "broken run"
    assert bench.taken_back == []


class FakeShell:
    def __init__(self, answers):
        self.answers, self.commands = answers, []

    def __call__(self, command, cwd=None):
        self.commands.append((list(command), cwd))
        joined = " ".join(command)
        for pattern, answer in self.answers.items():
            if pattern in joined:
                return answer
        return ""


class SignedOutShell(FakeShell):
    def __call__(self, command, cwd=None):
        if command[1:] == ["auth", "status"]:
            raise main_verifier.CommandFailed('claude.exe auth status: {"loggedIn": false}')
        return super().__call__(command, cwd)


def test_taking_a_change_back_reverts_its_pull_requests_commits_newest_first_and_opens_a_pull_request(tmp_path):
    shell = FakeShell({
        "commits/b2/pulls": "40", "commits/c3/pulls": "41", "commits/d4/pulls": "41",
        "pr view 41": "Loop the row on the Main Funestra",
        "pr create": "https://github.com/haglio/fun_time/pull/77\n",
    })
    bench = main_verifier.MachineBench(primary=tmp_path / "fun_time", shell=shell)

    assert bench.take_back("c3", ["b2", "c3", "d4"], ["tests/integration/test_x.py::test_y"]) == main_verifier.TakeBack(
        41, "Loop the row on the Main Funestra", 77)

    reverts = [command for command, _ in shell.commands if command[:2] == ["git", "revert"]]
    assert reverts == [["git", "revert", "--no-edit", "d4", "c3"]]
    created = next(command for command, _ in shell.commands if command[:3] == ["gh", "pr", "create"])
    assert "Take back #41: Loop the row on the Main Funestra" in created


def test_shipping_a_change_again_reverts_what_took_it_back_newest_first_in_a_pull_request_of_its_own(tmp_path):
    shell = FakeShell({"pr view 41": "Loop the row on the Main Funestra",
                       "pr create": "https://github.com/haglio/fun_time/pull/79\n"})
    primary = tmp_path / "fun_time"
    bench = main_verifier.MachineBench(primary=primary, shell=shell)

    assert bench.ship_again(41, ["d4", "e5"], ["tests/integration/test_x.py::test_y"]) == 79

    place = primary / ".claude" / "worktrees" / "ship-again-41"
    branched = ["git", "worktree", "add", "-B", "claude/ship-again-41", str(place), "origin/main"]
    assert (branched, primary) in shell.commands
    assert (["git", "revert", "--no-edit", "e5", "d4"], place) in shell.commands
    created = next(command for command, _ in shell.commands if command[:3] == ["gh", "pr", "create"])
    assert "Ship #41 again: Loop the row on the Main Funestra" in created
    assert "tests/integration/test_x.py::test_y" in created[created.index("--body") + 1]


def test_a_fix_session_is_stopped_with_the_claude_command_line(tmp_path):
    shell = FakeShell({})

    main_verifier.MachineBench(primary=tmp_path / "fun_time", shell=shell, claude="claude.exe").stop_fix_session(
        "1a2b3c4d")

    assert shell.commands == [(["claude.exe", "stop", "1a2b3c4d"], None)]


def test_a_run_checks_main_out_where_the_runner_keeps_it_and_names_the_tests_that_failed(tmp_path):
    shell = FakeShell({})
    suites = []

    def suite(command, cwd, log, asked_to_restart):
        suites.append((command, cwd, asked_to_restart))
        log.write_text("FAILED tests\\integration\\test_x.py::test_y - AssertionError\n", encoding="utf-8")
        return 1

    primary = tmp_path / "fun_time"
    bench = main_verifier.MachineBench(primary=primary, shell=shell, suite=suite)

    result = bench.run("c3", ["tests/integration/test_x.py::test_y"])

    place = primary / ".claude" / "worktrees" / "verifying-main"
    assert ["git", "worktree", "add", "--detach", str(place), "c3"] in [command for command, _ in shell.commands]
    assert (place / "state" / "genau_project_dirs.txt").read_text(encoding="utf-8") == ""
    command, cwd, asked_to_restart = suites[0]
    assert command[1:] == ["-m", "tests.integration.hidden_desktop", "tests/integration/test_x.py::test_y"]
    assert cwd == place
    assert asked_to_restart == bench.asked_to_restart
    assert result.exit_code == 1 and result.failed == ["tests/integration/test_x.py::test_y"]
    assert "test_x.py::test_y" in result.log.read_text(encoding="utf-8")


def test_the_tests_a_run_failed_are_read_off_its_summary_lines():
    output = "\n".join([
        "tests\\integration\\test_crown_integration.py ..F.                [ 10%]",
        "=========================== short test summary info ===========================",
        "FAILED tests\\integration\\test_crown_integration.py::test_one_crown - AssertionError: Timed out",
        "ERROR tests/integration/test_quit_integration.py::test_quit[fast] - OSError: [WinError 32]",
        "FAILED tests\\integration\\test_crown_integration.py::test_one_crown - AssertionError: again",
        "========== 1 failed, 110 passed, 1 error in 640.10s (0:10:40) ==========",
    ])

    assert main_verifier.failed_tests(output) == [
        "tests/integration/test_crown_integration.py::test_one_crown",
        "tests/integration/test_quit_integration.py::test_quit[fast]",
    ]


def test_main_s_head_is_read_after_fetching_it(tmp_path):
    shell = FakeShell({"rev-parse origin/main": "e5\n"})
    primary = tmp_path / "fun_time"
    bench = main_verifier.MachineBench(primary=primary, shell=shell)

    assert bench.head() == "e5"
    assert shell.commands == [(["git", "fetch", "origin", "main"], primary),
                              (["git", "rev-parse", "origin/main"], primary)]


def test_a_commit_that_passed_is_written_where_the_production_updater_reads_it(tmp_path):
    primary = tmp_path / "fun_time"
    (primary / "pyproject.toml").parent.mkdir(parents=True)
    (primary / "pyproject.toml").write_text(REPO_PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8")

    main_verifier.MachineBench(primary=primary, shell=FakeShell({})).release_to_production("c3")

    assert (primary / _the_commit_file()).read_text(encoding="utf-8") == "c3"


def _the_commit_file():
    return tomllib.loads(REPO_PYPROJECT.read_text(encoding="utf-8"))["tool"]["haglio"]["production"]["commit-file"]


def test_a_head_that_passes_while_its_release_cannot_be_written_still_counts_as_verified(tmp_path):
    class HeldBench(FakeBench):
        def release_to_production(self, commit):
            raise OSError("could not replace state/main_verifier_passed.txt")

    bench = HeldBench("c3", ["a1", "b2", "c3"])
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "passed"

    assert ledger.verified == "c3"
    assert bench.recorded == [{"kind": "release not written", "commit": "c3",
                               "why": "could not replace state/main_verifier_passed.txt"}]


def test_each_incident_is_added_to_the_runners_record_with_when_it_happened(tmp_path):
    primary = tmp_path / "fun_time"
    bench = main_verifier.MachineBench(primary=primary, shell=FakeShell({}))

    bench.record({"kind": "flaky", "commit": "c3", "log": primary / "state" / "run.txt"})
    bench.record({"kind": "broken run", "commit": "d4"})

    lines = (primary / "state" / "main_verifier_incidents.jsonl").read_text(encoding="utf-8").splitlines()
    first, second = (json.loads(line) for line in lines)
    assert (first["kind"], first["commit"], first["log"]) == ("flaky", "c3", str(primary / "state" / "run.txt"))
    assert second["kind"] == "broken run"
    assert datetime.fromisoformat(first["at"]) <= datetime.fromisoformat(second["at"])


def test_the_incidents_recorded_are_read_back_in_order_past_a_line_cut_short(tmp_path):
    primary = tmp_path / "fun_time"
    bench = main_verifier.MachineBench(primary=primary, shell=FakeShell({}))
    bench.record({"kind": "flaky", "commit": "c3"})
    with (primary / "state" / "main_verifier_incidents.jsonl").open("a", encoding="utf-8") as sink:
        sink.write('{"kind": "broke ma\n')
    bench.record({"kind": "broken run", "commit": "d4"})

    assert [(incident["kind"], incident["commit"]) for incident in bench.incidents()] == [
        ("flaky", "c3"), ("broken run", "d4")]


def test_taking_a_change_back_removes_the_checkout_it_made_for_it(tmp_path):
    shell = FakeShell({"commits/c3/pulls": "41", "pr create": "https://github.com/haglio/fun_time/pull/77\n"})
    primary = tmp_path / "fun_time"

    main_verifier.MachineBench(primary=primary, shell=shell).take_back("c3", ["c3"], [])

    place = primary / ".claude" / "worktrees" / "take-back-41"
    assert (["git", "worktree", "remove", "--force", str(place)], primary) in shell.commands


def test_a_restart_is_asked_for_with_a_file_in_the_production_checkouts_state(tmp_path):
    primary = tmp_path / "fun_time"
    bench = main_verifier.MachineBench(primary=primary, shell=FakeShell({}))
    assert not bench.take_restart_request()

    (primary / "state").mkdir(parents=True)
    (primary / "state" / "main_verifier.restart").write_text("", encoding="utf-8")

    assert bench.asked_to_restart()
    assert bench.take_restart_request()
    assert not bench.asked_to_restart() and not bench.take_restart_request()


def test_a_suite_running_when_a_restart_is_asked_for_ends_with_everything_it_started(tmp_path, monkeypatch):
    monkeypatch.setattr(main_verifier, "_ASKED_EVERY_S", 0.05)
    its_pid = tmp_path / "pid.txt"
    sleeper = f"import os, pathlib, time; pathlib.Path(r'{its_pid}').write_text(str(os.getpid())); time.sleep(60)"

    with pytest.raises(main_verifier.RestartRequested):
        main_verifier._run_suite([sys.executable, "-c", sleeper], tmp_path, tmp_path / "run.txt",
                                 asked_to_restart=its_pid.exists)

    assert not is_process_alive(int(its_pid.read_text(encoding="utf-8")))


def test_a_command_that_cannot_start_fails_like_one_that_ran_and_failed(tmp_path):
    with pytest.raises(main_verifier.CommandFailed, match="could not start"):
        commands.run_command([sys.executable, "-c", "pass"], cwd=tmp_path / "a checkout since removed")


def stop_after_two_naps():
    naps = []

    def nap(seconds):
        naps.append(seconds)
        if len(naps) == 2:
            raise KeyboardInterrupt

    return naps, nap


def test_main_is_checked_and_the_outcome_logged_each_time_the_runner_wakes(tmp_path, caplog):
    bench = FakeBench("c3", ["a1", "b2", "c3"], broken_from="c3")
    ledger = a_ledger(tmp_path, verified="a1")
    naps, nap = stop_after_two_naps()
    with caplog.at_level("INFO"), pytest.raises(KeyboardInterrupt):
        main_verifier.keep_verifying(ledger, bench, nap=nap)

    assert naps == [main_verifier.POLL_S] * 2
    assert [record.getMessage() for record in caplog.records] == ["main at c3: broke main", "main at c3: wait"]


@pytest.mark.parametrize("how_it_passed", [{}, {"broken_from": "c3", "flaky": True}], ids=["passed", "flaky"])
def test_the_runner_ends_once_a_head_passes_so_that_it_starts_again_running_the_code_it_passed(
        tmp_path, how_it_passed):
    bench = FakeBench("c3", ["a1", "b2", "c3"], **how_it_passed)

    main_verifier.keep_verifying(a_ledger(tmp_path, verified="a1"), bench, nap=never_naps)

    assert bench.released == ["c3"]


def test_the_runner_ends_without_checking_main_once_it_is_asked_to_restart(tmp_path):
    bench = FakeBench("c3", ["a1", "b2", "c3"], restart_asked=True)

    main_verifier.keep_verifying(a_ledger(tmp_path, verified="a1"), bench, nap=never_naps)

    assert bench.events == [] and bench.restart_asked is False


def test_a_run_cut_short_by_a_restart_request_ends_the_runner_without_judging_main(tmp_path):
    class AskedMidRun(FakeBench):
        def run(self, commit, tests=()):
            self.restart_asked = True
            raise main_verifier.RestartRequested

    bench = AskedMidRun("c3", ["a1", "b2", "c3"])
    ledger = a_ledger(tmp_path, verified="a1")

    main_verifier.keep_verifying(ledger, bench, nap=never_naps)

    assert (ledger.verified, ledger.failed_head) == ("a1", None)
    assert bench.recorded == [] and bench.restart_asked is False


def test_a_check_that_could_not_reach_main_is_logged_and_tried_again_next_time(tmp_path, caplog):
    class OfflineBench(FakeBench):
        def head(self):
            raise main_verifier.CommandFailed("git fetch origin main: could not resolve host")

    naps, nap = stop_after_two_naps()
    with caplog.at_level("INFO"), pytest.raises(KeyboardInterrupt):
        main_verifier.keep_verifying(a_ledger(tmp_path), OfflineBench("c3", ["c3"]), nap=nap)

    assert [record.getMessage() for record in caplog.records] == [
        "could not check main: git fetch origin main: could not resolve host"] * 2


def test_a_change_from_before_the_failing_test_existed_is_never_blamed_for_it(tmp_path):
    class NewTestBench(FakeBench):
        def run(self, commit, tests=()):
            if tests and self.commits.index(commit) < self.commits.index("d4"):
                return main_verifier.RunResult(main_verifier.NO_SUCH_TEST, [], WHOLE_RUN)
            return super().run(commit, tests)

    bench = NewTestBench("e5", ["a1", "b2", "c3", "d4", "e5"], broken_from="d4")

    assert main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench) == "broke main"

    assert bench.taken_back[0][0] == "d4"


def test_failures_that_cannot_be_run_again_by_name_take_nothing_back(tmp_path):
    class MisreadBench(FakeBench):
        def run(self, commit, tests=()):
            if tests:
                return main_verifier.RunResult(main_verifier.NO_SUCH_TEST, [], WHOLE_RUN)
            return super().run(commit, tests)

    bench = MisreadBench("c3", ["a1", "b2", "c3"], broken_from="c3")
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "broken run"

    assert bench.taken_back == [] and ledger.failed_head == "c3"


def test_tests_that_now_fail_where_main_last_passed_are_blamed_on_no_change_that_shipped_since(tmp_path):
    bench = FakeBench("e5", ["a1", "b2", "c3", "d4", "e5"], broken_from="a1")
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "broken outside main"

    assert bench.taken_back == [] and bench.fixing == []
    assert ledger.failed_head == "e5"
    assert bench.recorded == [{"kind": "broken outside main", "commit": "e5", "last_passed": "a1",
                               "tests": ["tests/integration/test_x.py::test_y"], "log": WHOLE_RUN}]


def test_a_change_is_taken_back_once_the_failing_tests_fail_every_run_with_it_and_pass_every_run_without_it(
        tmp_path):
    bench = FakeBench("e5", ["a1", "b2", "c3", "d4", "e5"], broken_from="c3")

    assert main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench) == "broke main"

    assert bench.taken_back[0][0] == "c3"
    assert bench.events.count(("run", "c3")) == bench.events.count(("run", "b2")) == 1 + main_verifier.CONFIRMING_RUNS


def test_the_first_change_main_has_not_passed_since_is_pinned_on_its_own_runs_when_no_commit_passed_before_it(
        tmp_path):
    bench = FakeBench("c3", ["a1", "b2", "c3"], broken_from="a1")

    assert main_verifier.cycle(a_ledger(tmp_path), bench) == "broke main"

    assert bench.taken_back[0][0] == "a1"
    assert bench.events.count(("run", "a1")) == 1 + main_verifier.CONFIRMING_RUNS


@pytest.mark.parametrize("reruns_failing", [{"c3": [True, True, False]}, {"b2": [False, False, True]}],
                         ids=["passing once with the change", "failing once without it"])
def test_tests_that_do_not_fail_every_run_with_the_change_and_pass_every_run_without_it_take_nothing_back(
        tmp_path, reruns_failing):
    bench = FakeBench("e5", ["a1", "b2", "c3", "d4", "e5"], broken_from="c3", reruns_failing=reruns_failing)
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "unreliable check"

    assert bench.taken_back == [] and bench.released == []
    assert ledger.failed_head == "e5"
    [fix] = bench.fixing
    assert "tests/integration/test_x.py::test_y" in fix.brief
    assert bench.recorded == [{"kind": "unreliable check", "commit": "e5",
                               "tests": ["tests/integration/test_x.py::test_y"], "log": WHOLE_RUN,
                               "suspected": "c3", "fix_session": "812181fb"}]


UNRELIABLE = {"kind": "unreliable check", "commit": "b2", "tests": ["tests/integration/test_x.py::test_y"],
              "fix_session": "1a2b3c4d"}


@pytest.mark.parametrize("its_state", ["working", "blocked", "failed"])
def test_no_second_session_starts_for_unreliable_tests_while_one_is_still_fixing_them(tmp_path, its_state):
    bench = FakeBench("e5", ["a1", "b2", "c3", "d4", "e5"], broken_from="c3", reruns_failing={"c3": [True, False]},
                      incidents=[UNRELIABLE], sessions={"1a2b3c4d": its_state})

    assert main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench) == "unreliable check"

    assert bench.fixing == []
    assert bench.recorded[0]["being_fixed_by"] == "1a2b3c4d"


def test_a_new_session_starts_for_unreliable_tests_once_the_last_one_on_them_has_finished(tmp_path):
    bench = FakeBench("e5", ["a1", "b2", "c3", "d4", "e5"], broken_from="c3", reruns_failing={"c3": [True, False]},
                      incidents=[UNRELIABLE], sessions={"1a2b3c4d": "done"})

    assert main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench) == "unreliable check"

    assert len(bench.fixing) == 1 and bench.recorded[0]["fix_session"] == "812181fb"


TAKEN_BACK = {"kind": "broke main", "commit": "c3", "tests": ["tests/integration/test_x.py::test_y"],
              "taken_back_by": 77, "pull_request": 41, "fix_session": "1a2b3c4d"}


def test_tests_that_had_a_change_taken_back_and_fail_again_without_it_ship_it_again_and_get_a_session_to_fix_them(
        tmp_path):
    bench = FakeBench("d4", ["a1", "c3", "d4"], broken_from="c3", incidents=[TAKEN_BACK],
                      pull_requests={"c3": 41, "d4": 77})
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "unreliable check"

    assert bench.shipped_again == [(41, ["d4"])] and bench.taken_back == []
    assert bench.stopped == ["1a2b3c4d"]
    assert ledger.failed_head == "d4"
    [fix] = bench.fixing
    assert "#41" in fix.brief
    assert bench.recorded == [{"kind": "unreliable check", "commit": "d4",
                               "tests": ["tests/integration/test_x.py::test_y"], "log": WHOLE_RUN,
                               "pull_request": 41, "shipped_again_by": 79, "fix_session": "812181fb"}]


SHIPPED_AGAIN = {"kind": "unreliable check", "commit": "d4", "tests": ["tests/integration/test_x.py::test_y"],
                 "pull_request": 41, "shipped_again_by": 79}


@pytest.mark.parametrize("earlier, pull_requests, tests", [
    ([TAKEN_BACK, SHIPPED_AGAIN], {"c3": 41, "d4": 77}, ("tests/integration/test_x.py::test_y",)),
    ([TAKEN_BACK], {"c3": 41, "d4": 50}, ("tests/integration/test_x.py::test_y",)),
    ([TAKEN_BACK], {"c3": 41, "d4": 77},
     ("tests/integration/test_x.py::test_y", "tests/integration/test_q.py::test_r")),
], ids=["shipped again already", "taken back before main last passed", "failing other tests too"])
def test_a_taken_back_change_is_shipped_again_only_when_its_tests_alone_fail_on_a_main_that_just_took_it_out(
        tmp_path, earlier, pull_requests, tests):
    bench = FakeBench("d4", ["a1", "c3", "d4"], broken_from="c3", tests=tests, incidents=earlier,
                      pull_requests=pull_requests)

    assert main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench) == "broke main"

    assert bench.shipped_again == [] and bench.stopped == []


def test_a_change_that_could_not_be_shipped_again_leaves_the_session_fixing_it_running(tmp_path):
    class NoPullRequestBench(FakeBench):
        def ship_again(self, pull_request, take_back, tests):
            return None

    bench = NoPullRequestBench("d4", ["a1", "c3", "d4"], broken_from="c3", incidents=[TAKEN_BACK],
                               pull_requests={"c3": 41, "d4": 77})

    assert main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench) == "unreliable check"

    assert bench.stopped == []
    assert bench.recorded[0]["shipped_again_by"] is None


def test_a_session_fixing_a_change_shipped_again_that_could_not_be_stopped_is_recorded_with_why(tmp_path):
    class UnstoppableBench(FakeBench):
        def stop_fix_session(self, session):
            raise main_verifier.CommandFailed("claude.exe stop 1a2b3c4d: no such session")

    bench = UnstoppableBench("d4", ["a1", "c3", "d4"], broken_from="c3", incidents=[TAKEN_BACK],
                             pull_requests={"c3": 41, "d4": 77})

    assert main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench) == "unreliable check"

    assert bench.recorded[0]["fix_session_not_stopped"] == "claude.exe stop 1a2b3c4d: no such session"
    assert bench.recorded[0]["fix_session"] == "812181fb"


def test_unreliable_tests_whose_fixing_cannot_be_looked_up_are_recorded_with_why(tmp_path):
    class NoSessionsBench(FakeBench):
        def fix_session_states(self):
            raise main_verifier.CommandFailed("claude agents did not answer with a list of sessions: ")

    bench = NoSessionsBench("e5", ["a1", "b2", "c3", "d4", "e5"], broken_from="c3",
                            reruns_failing={"c3": [True, False]})

    assert main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench) == "unreliable check"

    assert bench.fixing == []
    assert bench.recorded[0]["fix_session_not_started"].startswith("claude agents did not answer")


def test_a_session_to_fix_unreliable_tests_works_on_a_branch_of_its_own_and_is_told_which_fail_and_where(tmp_path):
    tests = ["tests/integration/test_x.py::test_y", "tests/integration/test_z.py::test_w"]

    fix = main_verifier.fixing_unreliable_checks("e5f6a7b8c9", tests, Path("C:/runs/e5.txt"),
                                                 "They passed on some runs and failed on others.")

    assert fix.checkout == "fix-unreliable-e5f6a7b8"
    assert fix.title == "Fix the unreliable test_x and test_z"
    for detail in (*tests, str(Path("C:/runs/e5.txt")), "claude/fix-unreliable-e5f6a7b8",
                   "They passed on some runs and failed on others."):
        assert detail in fix.brief


@pytest.mark.parametrize("the_verifier_s_own_coverage_file", [None, "elsewhere.coverage"])
def test_coverage_is_measured_one_integration_test_file_at_a_time_and_kept_for_branches_to_pick_from(
        tmp_path, monkeypatch, the_verifier_s_own_coverage_file):
    if the_verifier_s_own_coverage_file:
        monkeypatch.setenv("COVERAGE_FILE", str(tmp_path / the_verifier_s_own_coverage_file))
    else:
        monkeypatch.delenv("COVERAGE_FILE", raising=False)
    primary = tmp_path / "fun_time"
    checkout = primary / ".claude" / "worktrees" / "verifying-main"
    for name in ("tests/integration/test_a.py", "tests/integration/test_b.py", "fun_time/x.py", "fun_time/y.py"):
        (checkout / name).parent.mkdir(parents=True, exist_ok=True)
        (checkout / name).write_text("", encoding="utf-8")
    shell = FakeShell({"ls-files -- tests/integration/test_*.py": "tests/integration/test_a.py\ntests/integration/test_b.py\n",
                       "ls-files -- *.py": "fun_time/x.py\nfun_time/y.py\ntests/integration/test_a.py\n"
                                           "tests/integration/test_b.py\ntests/test_unit.py\n"})
    runs = []

    def suite(command, cwd, log, environment, asked_to_restart):
        runs.append(command[3:])
        assert asked_to_restart == bench.asked_to_restart
        with patch.dict(os.environ, environment, clear=True):
            data_file = coverage.Coverage(config_file=environment["COVERAGE_PROCESS_START"]).config.data_file
        data = coverage.CoverageData(basename=data_file, suffix=True)
        ran, only_found = ("x.py", "y.py") if command[3].endswith("test_a.py") else ("y.py", "x.py")
        data.add_lines({str(checkout / "fun_time" / ran): [1]})
        data.touch_files([str(checkout / "fun_time" / only_found)])
        data.write()
        log.write_text("", encoding="utf-8")
        return 0

    bench = main_verifier.MachineBench(primary=primary, shell=shell, suite=suite)
    bench.measure_coverage("c3")

    assert runs ==[["tests/integration/test_a.py", "--no-cov"], ["tests/integration/test_b.py", "--no-cov"]]
    assert CoverageMap.load(primary / "state" / "integration_coverage_map.json") == CoverageMap(
        commit="c3",
        ran={"tests/integration/test_a.py": frozenset({"fun_time/x.py"}),
             "tests/integration/test_b.py": frozenset({"fun_time/y.py"})})


def _tear_the_lines_saved_in(data_file: str) -> None:
    with closing(sqlite3.connect(data_file)) as data:
        page_size, = data.execute("pragma page_size").fetchone()
        lines_page, = data.execute("select rootpage from sqlite_master where name = 'line_bits'").fetchone()
    with open(data_file, "r+b") as raw:
        raw.seek((lines_page - 1) * page_size)
        raw.write(b"\xff" * page_size)


def _leave_its_tables_half_made(data_file: str) -> None:
    os.remove(data_file)
    with closing(sqlite3.connect(data_file)) as data:
        data.execute("create table file (id integer primary key, path text, unique (path))")
        data.commit()


@pytest.mark.parametrize("tear", [_tear_the_lines_saved_in, _leave_its_tables_half_made])
def test_a_process_ended_part_way_through_saving_costs_the_map_only_what_that_process_ran(tmp_path, caplog, tear):
    checkout, measuring = tmp_path / "verifying-main", tmp_path / "measuring"
    measuring.mkdir()
    torn = None
    for ran in ("x.py", "y.py"):
        data = coverage.CoverageData(basename=str(measuring / ".coverage"), suffix=True)
        data.add_lines({str(checkout / "fun_time" / ran): [1]})
        data.write()
        torn = Path(data.data_filename())
    tear(str(torn))

    with caplog.at_level("WARNING", logger="main_verifier"):
        ran = main_verifier.measured_sources(measuring / ".coverage", checkout,
                                             frozenset({"fun_time/x.py", "fun_time/y.py"}))

    assert ran == {"fun_time/x.py"}
    assert torn.name in caplog.text


def test_a_process_file_the_map_could_not_open_is_let_go_while_the_warning_is_kept(tmp_path, caplog):
    measuring = tmp_path / "measuring"
    measuring.mkdir()
    data = coverage.CoverageData(basename=str(measuring / ".coverage"), suffix=True)
    data.add_lines({str(tmp_path / "fun_time" / "x.py"): [1]})
    data.write()
    torn = Path(data.data_filename())
    with open(torn, "r+b") as raw:
        raw.write(b"\xff" * 16)

    with caplog.at_level("WARNING", logger="main_verifier"):
        main_verifier.measured_sources(measuring / ".coverage", tmp_path, frozenset({"fun_time/x.py"}))

    os.remove(torn)


def test_a_head_that_passes_is_measured_for_coverage_once_the_map_is_out_of_date(tmp_path):
    fresh, stale = FakeBench("c3", ["a1", "b2", "c3"]), FakeBench("c3", ["a1", "b2", "c3"], stale_map=True)
    broken = FakeBench("c3", ["a1", "b2", "c3"], broken_from="c3", stale_map=True)

    for bench in (fresh, stale, broken):
        main_verifier.cycle(a_ledger(tmp_path / str(id(bench)), verified="a1"), bench)

    assert (fresh.measured, stale.measured, broken.measured) == ([], ["c3"], [])


def test_the_coverage_map_is_out_of_date_when_missing_or_a_day_old(tmp_path):
    primary = tmp_path / "fun_time"
    bench = main_verifier.MachineBench(primary=primary, shell=FakeShell({}))
    kept = primary / "state" / "integration_coverage_map.json"

    assert bench.coverage_map_is_stale()
    kept.parent.mkdir(parents=True)
    kept.write_text("{}", encoding="utf-8")
    assert not bench.coverage_map_is_stale()
    a_day_ago = kept.stat().st_mtime - main_verifier.COVERAGE_MAP_LASTS.total_seconds()
    os.utime(kept, (a_day_ago, a_day_ago))
    assert bench.coverage_map_is_stale()


PINS = """\
[project]
name = "fun-time"
dependencies = [
    "app-support @ git+https://github.com/haglio/app_support@v0.1.182",
    "funestra @ git+https://github.com/haglio/funestra@v0.1.410",
    "shared-ui @ git+https://github.com/haglio/shared_ui@v0.1.159",
]
[project.optional-dependencies]
dev = ["pytest>=9,<10"]
windows = ["comtypes>=1.4.17,<2"]
"""


def _a_verifiers_installation(primary):
    venv = primary / "state" / main_verifier.VERIFIER_VENV
    (venv / "Scripts").mkdir(parents=True)
    (venv / "Scripts" / "python.exe").write_bytes(b"")
    return venv


def _installs(shell):
    return [command for command, _ in shell.commands if command[1:4] == ["-m", "pip", "install"]]


def test_a_missing_installation_is_made_and_given_everything_the_head_declares(tmp_path):
    shell = FakeShell({"c3:pyproject.toml": PINS})
    primary = tmp_path / "fun_time"
    venv = primary / "state" / main_verifier.VERIFIER_VENV

    main_verifier.MachineBench(primary=primary, shell=shell, base_python="C:/Python/python.exe").install_for("c3")

    checkout = primary / ".claude" / "worktrees" / "verifying-main"
    assert ["C:/Python/python.exe", "-m", "venv", str(venv)] in [command for command, _ in shell.commands]
    assert _installs(shell) == [[str(venv / "Scripts" / "python.exe"), "-m", "pip", "install", "-e",
                                 f"{checkout}[dev,windows]", "--config-settings", "editable_mode=compat"]]
    assert (venv / main_verifier.INSTALLED_FROM).read_text(encoding="utf-8") == PINS


def test_a_head_whose_pyproject_moved_is_installed_again_whole_its_pins_exactly(tmp_path):
    shell = FakeShell({"c3:pyproject.toml": PINS})
    primary = tmp_path / "fun_time"
    venv = _a_verifiers_installation(primary)
    (venv / main_verifier.INSTALLED_FROM).write_text(PINS.replace("v0.1.410", "v0.1.409"), encoding="utf-8")

    main_verifier.MachineBench(primary=primary, shell=shell).install_for("c3")

    assert len(_installs(shell)) == 1
    assert (venv / main_verifier.INSTALLED_FROM).read_text(encoding="utf-8") == PINS


def test_an_installation_made_from_the_heads_own_pyproject_is_not_made_again(tmp_path):
    shell = FakeShell({"c3:pyproject.toml": PINS})
    primary = tmp_path / "fun_time"
    venv = _a_verifiers_installation(primary)
    (venv / main_verifier.INSTALLED_FROM).write_text(PINS, encoding="utf-8")

    main_verifier.MachineBench(primary=primary, shell=shell).install_for("c3")

    assert _installs(shell) == []


def test_the_suite_runs_out_of_the_verifiers_own_installation(tmp_path):
    suites = []

    def suite(command, cwd, log, environment=None, *, asked_to_restart):
        suites.append(command)
        log.write_text("", encoding="utf-8")
        return 0

    primary = tmp_path / "fun_time"
    bench = main_verifier.MachineBench(primary=primary, shell=FakeShell({}), suite=suite)

    bench.run("c3")

    assert suites[0][0] == str(primary / "state" / main_verifier.VERIFIER_VENV / "Scripts" / "python.exe")


def test_a_fix_session_starts_in_a_checkout_of_its_own_and_is_told_what_broke(tmp_path):
    shell = FakeShell({"auth status": '{"loggedIn": true}',
                       "--bg": "backgrounded \xb7 812181fb \xb7 Fix #41: Loop the row on the Main Funestra\n"})
    primary = tmp_path / "fun_time"
    bench = main_verifier.MachineBench(primary=primary, shell=shell, claude="claude.exe")
    taken = main_verifier.TakeBack(41, "Loop the row on the Main Funestra", 77)

    started = bench.start_fix_session(
        main_verifier.fixing_a_take_back(taken, ["tests/integration/test_x.py::test_y"], Path("C:/runs/d4.txt")))

    assert started == "812181fb"
    place = primary / ".claude" / "worktrees" / "fix-41"
    assert (["git", "worktree", "add", "-B", "claude/fix-41", str(place), "origin/main"], primary) in shell.commands
    assert (place / "state" / "genau_project_dirs.txt").read_text(encoding="utf-8") == ""
    command, cwd = next((command, cwd) for command, cwd in shell.commands if "--bg" in command)
    assert command[0] == "claude.exe" and cwd == place
    assert command[command.index("--permission-mode") + 1] == "auto"
    assert command[command.index("--name") + 1] == "Fix #41: Loop the row on the Main Funestra"
    brief = command[-1]
    for detail in ("#41", "Loop the row on the Main Funestra", "tests/integration/test_x.py::test_y", "#77",
                   str(Path("C:/runs/d4.txt")), "claude/fix-41"):
        assert detail in brief


def test_each_background_session_s_state_is_read_off_the_claude_command_line(tmp_path):
    sessions = [{"id": "1a2b3c4d", "kind": "background", "state": "working"},
                {"pid": 145304, "kind": "interactive", "status": "idle"}]
    shell = FakeShell({"agents --json --all": json.dumps(sessions)})
    bench = main_verifier.MachineBench(primary=tmp_path / "fun_time", shell=shell, claude="claude.exe")

    assert bench.fix_session_states() == {"1a2b3c4d": "working"}
    assert shell.commands == [(["claude.exe", "agents", "--json", "--all"], None)]


def test_a_session_list_that_is_not_one_fails_like_a_command_that_failed(tmp_path):
    bench = main_verifier.MachineBench(primary=tmp_path / "fun_time", shell=FakeShell({"agents": "Not signed in"}),
                                       claude="claude.exe")

    with pytest.raises(main_verifier.CommandFailed, match="Not signed in"):
        bench.fix_session_states()


def test_no_fix_session_starts_while_claude_code_is_not_signed_in(tmp_path):
    shell = SignedOutShell({})
    bench = main_verifier.MachineBench(primary=tmp_path / "fun_time", shell=shell, claude="claude.exe")

    with pytest.raises(main_verifier.CommandFailed, match="not signed in"):
        bench.start_fix_session(main_verifier.fixing_a_take_back(
            main_verifier.TakeBack(41, "Loop the row on the Main Funestra", 77), [], Path("run.txt")))

    assert not [command for command, _ in shell.commands if command[:3] == ["git", "worktree", "add"]]


def test_fix_sessions_run_on_the_newest_claude_code_the_claude_app_keeps(tmp_path, monkeypatch):
    kept = tmp_path / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude" / "claude-code"
    for version, folder in (("2.1.289", "e1f0154146bb"), ("2.1.293", "83cb0bd7fed4"), ("2.1.30", "0c0ffee0")):
        (kept / version / folder).mkdir(parents=True)
        (kept / version / folder / "claude.exe").write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert main_verifier.claude_command() == str(kept / "2.1.293" / "83cb0bd7fed4" / "claude.exe")


FIX = {"kind": "broke main", "commit": "aaaa1111", "taken_back_by": 399, "pull_request": 398,
       "fix_session": "1a2b3c4d"}


def a_fix_session(state, **more):
    return {"id": "1a2b3c4d", "sessionId": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d", "kind": "background",
            "name": "Fix #398: Keep the crown lit",
            "cwd": "C:/fun_time/.claude/worktrees/fix-398", "state": state, **more}


def notices_about(session, told):
    return fix_sessions.fix_session_steps([FIX], [session], told, restarts={}, died=lambda session: False).notices


def test_he_is_told_once_when_a_fix_session_waits_for_him_and_once_when_it_finishes():
    waiting = a_fix_session("blocked", waitingFor="input needed")

    [notice] = notices_about(waiting, told={})
    assert (notice.title, notice.state) == ("Fix #398: Keep the crown lit is waiting for you", "blocked")
    assert notice.resume == "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"
    assert notice.body == "It needs your answer. Click to open it in Claude Desktop."
    assert notices_about(waiting, told={"1a2b3c4d": "blocked"}) == []
    [finished] = notices_about(a_fix_session("done"), told={"1a2b3c4d": "blocked"})
    assert finished.title == "Fix #398: Keep the crown lit finished"
    assert notices_about(a_fix_session("busy"), told={}) == []


def test_a_fix_session_that_stopped_on_an_error_from_the_claude_service_is_started_again_instead_of_told_about():
    stopped = a_fix_session("blocked")

    steps = fix_sessions.fix_session_steps([FIX], [stopped], told={}, restarts={}, died=lambda session: True)

    assert steps.start_again == [stopped] and steps.notices == []


@pytest.mark.parametrize("waiting_for, needs", [(None, "answer"), ("input needed", "answer"),
                                                ("permission prompt", "permission")])
def test_a_fix_session_blocked_on_anything_but_an_error_is_waiting_for_him(waiting_for, needs):
    session = a_fix_session("blocked", **({"waitingFor": waiting_for} if waiting_for else {}))

    steps = fix_sessions.fix_session_steps([FIX], [session], told={}, restarts={},
                                            died=lambda session: "waitingFor" in session)

    assert steps.start_again == []
    assert [notice.body for notice in steps.notices] == [f"It needs your {needs}. Click to open it in Claude Desktop."]


def test_a_fix_session_that_failed_is_started_again_like_one_that_stopped_on_an_error_from_the_service():
    failed = a_fix_session("failed")

    steps = fix_sessions.fix_session_steps([FIX], [failed], told={}, restarts={}, died=lambda session: False)

    assert steps.start_again == [failed] and steps.notices == []


def test_he_is_told_a_fix_session_stopped_on_an_error_once_starting_it_again_has_not_helped():
    steps = fix_sessions.fix_session_steps([FIX], [a_fix_session("blocked")], told={},
                                            restarts={"1a2b3c4d": fix_sessions.RESTARTS}, died=lambda session: True)

    assert steps.start_again == []
    assert [(notice.title, notice.state) for notice in steps.notices] == [
        ("Fix #398: Keep the crown lit stopped on an error", "blocked")]


def test_a_stopped_fix_session_he_was_told_is_waiting_for_him_is_left_to_him():
    steps = fix_sessions.fix_session_steps([FIX], [a_fix_session("blocked")], told={"1a2b3c4d": "blocked"},
                                            restarts={}, died=lambda session: True)

    assert steps == fix_sessions.FixSessionSteps(start_again=[], notices=[])


def test_a_fix_session_that_stops_on_an_error_after_he_was_told_it_finished_is_told_about_not_started_again():
    steps = fix_sessions.fix_session_steps([FIX], [a_fix_session("blocked")], told={"1a2b3c4d": "done"},
                                            restarts={}, died=lambda session: True)

    assert steps.start_again == []
    assert [notice.title for notice in steps.notices] == ["Fix #398: Keep the crown lit stopped on an error"]


ASKED = {"type": "user", "message": {"role": "user", "content": "Fix the crown."}}
ANSWERED = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Ship it?"}]}}
FAILED_BY_THE_SERVICE = {"type": "assistant", "isApiErrorMessage": True, "error": "server_error",
                         "message": {"role": "assistant", "content": [{"type": "text", "text": "API unavailable"}]}}
TURN_ENDED = {"type": "system", "subtype": "turn_duration"}


def write_transcript(transcripts, session, *records):
    kept = transcripts / "C--fun-time--claude-worktrees-fix-398" / f"{session['sessionId']}.jsonl"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


@pytest.mark.parametrize("records, died", [
    ((), False),
    ((ASKED, FAILED_BY_THE_SERVICE, TURN_ENDED), True),
    ((ASKED, FAILED_BY_THE_SERVICE, ASKED, ANSWERED, TURN_ENDED), False),
], ids=["no transcript", "an error last", "an answer after an error"])
def test_a_session_died_when_the_last_thing_it_said_was_an_error_from_the_claude_service(tmp_path, records, died):
    session = a_fix_session("blocked")
    if records:
        write_transcript(tmp_path, session, *records)

    assert fix_sessions.died_on_a_service_error(tmp_path, session) is died


def test_a_record_that_started_no_session_is_never_matched_to_a_session_without_an_id():
    interactive = {"pid": 145304, "kind": "interactive", "status": "idle",
                   "sessionId": "9a8b7c6d-5e4f-3a2b-1c0d-9e8f7a6b5c4d"}

    steps = fix_sessions.fix_session_steps([{"kind": "flaky", "commit": "c3"}, FIX], [interactive], told={},
                                           restarts={}, died=lambda session: True)

    assert steps == fix_sessions.FixSessionSteps(start_again=[], notices=[])


@pytest.mark.parametrize("state", ["done", "blocked"])
def test_a_session_fixing_a_change_the_verifier_shipped_again_is_neither_told_about_nor_started_again(state):
    shipped_again = {"kind": "unreliable check", "pull_request": 398, "shipped_again_by": 400}

    steps = fix_sessions.fix_session_steps([FIX, shipped_again], [a_fix_session(state)], told={}, restarts={},
                                            died=lambda session: True)

    assert steps == fix_sessions.FixSessionSteps(start_again=[], notices=[])


def a_dead_fix_session(tmp_path):
    state, transcripts = tmp_path / "state", tmp_path / "projects"
    state.mkdir()
    session = a_fix_session("blocked")
    write_transcript(transcripts, session, ASKED, FAILED_BY_THE_SERVICE, TURN_ENDED)
    return state, transcripts, session


def test_a_fix_session_that_died_on_an_error_is_started_again_where_it_was_and_counted(tmp_path):
    state, transcripts, session = a_dead_fix_session(tmp_path)
    shell = FakeShell({"agents --json --all": json.dumps([session]), "auth status": '{"loggedIn": true}'})
    told = []

    fix_sessions.look_after_fix_sessions(state, [FIX], "claude.exe", shell=shell, start=told.append,
                                         transcripts=transcripts)

    assert told == []
    signed_in = (["claude.exe", "auth", "status"], None)
    resumed = (["claude.exe", "--bg", "--resume", session["sessionId"], fix_sessions.CARRY_ON], Path(session["cwd"]))
    assert shell.commands.index(signed_in) < shell.commands.index(resumed)
    assert json.loads((state / "main_verifier_fix_restarts.json").read_text(encoding="utf-8")) == {"1a2b3c4d": 1}


def test_a_fix_session_that_cannot_be_started_again_is_tried_next_time_and_the_others_are_still_told(
        tmp_path, caplog):
    state, transcripts, session = a_dead_fix_session(tmp_path)
    finished = {**a_fix_session("done"), "id": "9f8e7d6c", "name": "Fix the unreliable test_x"}
    shell = SignedOutShell({"agents --json --all": json.dumps([session, finished])})
    told = []

    with caplog.at_level("WARNING", logger="main_verifier"):
        fix_sessions.look_after_fix_sessions(state, [FIX, {**UNRELIABLE, "fix_session": "9f8e7d6c"}],
                                             "claude.exe", shell=shell, start=told.append,
                                             transcripts=transcripts)

    assert [notice.title for notice in told] == ["Fix the unreliable test_x finished"]
    assert not [command for command, _ in shell.commands if "--resume" in command]
    assert not (state / "main_verifier_fix_restarts.json").exists()
    assert "could not start fix session 1a2b3c4d again" in caplog.text


def test_each_new_state_of_a_fix_session_is_told_once_however_often_the_verifier_starts(tmp_path):
    sessions = [a_fix_session("blocked", waitingFor="input needed")]
    told = []

    def shell(command, cwd=None):
        assert command == ["claude.exe", "agents", "--json", "--all"]
        return json.dumps(sessions)

    for _ in range(2):
        fix_sessions.look_after_fix_sessions(tmp_path, [FIX], "claude.exe", shell=shell, start=told.append)
    sessions[0] = a_fix_session("done")
    fix_sessions.look_after_fix_sessions(tmp_path, [FIX], "claude.exe", shell=shell, start=told.append)

    assert [notice.state for notice in told] == ["blocked", "done"]
