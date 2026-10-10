from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import coverage
import pytest

from tests.integration import main_verifier
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


class FakeBench:
    def __init__(self, head, commits, broken_from=None, tests=("tests/integration/test_x.py::test_y",),
                 flaky=False, stale_map=False):
        self.head_commit, self.commits, self.broken_from = head, commits, broken_from
        self.tests, self.flaky, self.stale_map = list(tests), flaky, stale_map
        self.advanced, self.recorded, self.taken_back, self.measured = [], [], [], []
        self.fixing, self.events = [], []

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
        failed = self.tests if broken else []
        return main_verifier.RunResult(1 if broken else 0, failed, WHOLE_RUN)

    def advance_everyday_checkout(self, commit):
        self.advanced.append(commit)

    def take_back(self, commit, among, tests):
        self.taken_back.append((commit, list(among)))
        return main_verifier.TakeBack(41, "Loop the row on the main player", 77)

    def start_fix_session(self, taken, tests, log):
        self.fixing.append((taken, list(tests), log))
        return "812181fb"

    def bring_the_libraries_up_to(self, commit):
        self.events.append(("libraries", commit))

    def record(self, incident):
        self.recorded.append(incident)

    def coverage_map_is_stale(self):
        return self.stale_map

    def measure_coverage(self, commit):
        self.measured.append(commit)


def a_ledger(tmp_path, verified=None):
    ledger = main_verifier.Ledger(tmp_path / "main_verifier.json")
    if verified:
        ledger.passed(verified)
    return ledger


def test_a_head_that_passes_moves_the_everyday_checkout_to_it(tmp_path):
    bench = FakeBench("c3", ["a1", "b2", "c3"])
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "passed"
    assert bench.advanced == ["c3"]
    assert ledger.verified == "c3"


def test_a_head_that_breaks_takes_the_change_back_and_starts_a_session_to_fix_it(tmp_path):
    bench = FakeBench("e5", ["a1", "b2", "c3", "d4", "e5"], broken_from="c3")
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "broke main"

    assert bench.taken_back == [("c3", ["b2", "c3", "d4", "e5"])]
    taken = main_verifier.TakeBack(41, "Loop the row on the main player", 77)
    assert bench.fixing == [(taken, ["tests/integration/test_x.py::test_y"], WHOLE_RUN)]
    assert bench.advanced == []
    assert ledger.failed_head == "e5"
    assert bench.recorded == [{"kind": "broke main", "commit": "c3", "tests": ["tests/integration/test_x.py::test_y"],
                               "log": WHOLE_RUN, "taken_back_by": 77, "pull_request": 41,
                               "fix_session": "812181fb"}]


def test_a_fix_session_that_could_not_start_is_recorded_with_why(tmp_path):
    class UnsignedBench(FakeBench):
        def start_fix_session(self, taken, tests, log):
            raise main_verifier.CommandFailed("the Claude command line is not signed in")

    bench = UnsignedBench("c3", ["a1", "b2", "c3"], broken_from="c3")

    assert main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench) == "broke main"

    assert bench.recorded[0]["taken_back_by"] == 77
    assert bench.recorded[0]["fix_session_not_started"] == "the Claude command line is not signed in"


def test_main_s_libraries_are_brought_up_to_a_head_before_it_is_run(tmp_path):
    bench = FakeBench("c3", ["a1", "b2", "c3"])

    main_verifier.cycle(a_ledger(tmp_path, verified="a1"), bench)

    assert bench.events[:2] == [("libraries", "c3"), ("run", "c3")]


def test_a_failure_that_passes_when_rerun_is_recorded_as_flaky_and_main_still_moves(tmp_path):
    bench = FakeBench("c3", ["a1", "b2", "c3"], broken_from="c3", flaky=True)
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "flaky"

    assert bench.recorded[0]["kind"] == "flaky"
    assert bench.advanced == ["c3"] and bench.taken_back == []


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


def test_taking_a_change_back_reverts_its_pull_requests_commits_newest_first_and_opens_a_pull_request(tmp_path):
    shell = FakeShell({
        "commits/b2/pulls": "40", "commits/c3/pulls": "41", "commits/d4/pulls": "41",
        "pr view 41": "Loop the row on the main player",
        "pr create": "https://github.com/haglio/fun_time/pull/77\n",
    })
    bench = main_verifier.MachineBench(primary=tmp_path / "fun_time", shell=shell)

    assert bench.take_back("c3", ["b2", "c3", "d4"], ["tests/integration/test_x.py::test_y"]) == main_verifier.TakeBack(
        41, "Loop the row on the main player", 77)

    reverts = [command for command, _ in shell.commands if command[:2] == ["git", "revert"]]
    assert reverts == [["git", "revert", "--no-edit", "d4", "c3"]]
    created = next(command for command, _ in shell.commands if command[:3] == ["gh", "pr", "create"])
    assert "Take back #41: Loop the row on the main player" in created


def test_a_run_checks_main_out_where_the_runner_keeps_it_and_names_the_tests_that_failed(tmp_path):
    shell = FakeShell({})
    suites = []

    def suite(command, cwd, log):
        suites.append((command, cwd))
        log.write_text("FAILED tests\\integration\\test_x.py::test_y - AssertionError\n", encoding="utf-8")
        return 1

    primary = tmp_path / "fun_time"
    bench = main_verifier.MachineBench(primary=primary, shell=shell, suite=suite)

    result = bench.run("c3", ["tests/integration/test_x.py::test_y"])

    place = primary / ".claude" / "worktrees" / "verifying-main"
    assert ["git", "worktree", "add", "--detach", str(place), "c3"] in [command for command, _ in shell.commands]
    assert (place / "state" / "genau_project_dirs.txt").read_text(encoding="utf-8") == ""
    command, cwd = suites[0]
    assert command[1:] == ["-m", "tests.integration.hidden_desktop", "tests/integration/test_x.py::test_y"]
    assert cwd == place
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


def test_the_everyday_checkout_is_fast_forwarded_to_a_commit_that_passed(tmp_path):
    shell = FakeShell({"symbolic-ref": "main\n"})
    primary = tmp_path / "fun_time"

    main_verifier.MachineBench(primary=primary, shell=shell).advance_everyday_checkout("c3")

    assert (["git", "merge", "--ff-only", "c3"], primary) in shell.commands


def test_the_everyday_checkout_is_left_alone_while_it_has_changes_of_its_own(tmp_path):
    shell = FakeShell({"symbolic-ref": "main\n", "status --porcelain": " M fun_time/dashboard.py\n"})
    bench = main_verifier.MachineBench(primary=tmp_path / "fun_time", shell=shell)

    with pytest.raises(main_verifier.CommandFailed, match="uncommitted"):
        bench.advance_everyday_checkout("c3")

    assert not [command for command, _ in shell.commands if command[1] == "merge"]


def test_the_everyday_checkout_is_left_alone_while_it_is_off_main(tmp_path):
    shell = FakeShell({"symbolic-ref": "claude/some-branch\n"})
    bench = main_verifier.MachineBench(primary=tmp_path / "fun_time", shell=shell)

    with pytest.raises(main_verifier.CommandFailed, match="not on main"):
        bench.advance_everyday_checkout("c3")

    assert not [command for command, _ in shell.commands if command[1] == "merge"]


def test_a_head_that_passes_while_the_everyday_checkout_cannot_move_still_counts_as_verified(tmp_path):
    class HeldBench(FakeBench):
        def advance_everyday_checkout(self, commit):
            raise main_verifier.CommandFailed("the everyday checkout has uncommitted changes")

    bench = HeldBench("c3", ["a1", "b2", "c3"])
    ledger = a_ledger(tmp_path, verified="a1")

    assert main_verifier.cycle(ledger, bench) == "passed"

    assert ledger.verified == "c3"
    assert bench.recorded == [{"kind": "everyday checkout held", "commit": "c3",
                               "why": "the everyday checkout has uncommitted changes"}]


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


def test_taking_a_change_back_removes_the_checkout_it_made_for_it(tmp_path):
    shell = FakeShell({"commits/c3/pulls": "41", "pr create": "https://github.com/haglio/fun_time/pull/77\n"})
    primary = tmp_path / "fun_time"

    main_verifier.MachineBench(primary=primary, shell=shell).take_back("c3", ["c3"], [])

    place = primary / ".claude" / "worktrees" / "take-back-41"
    assert (["git", "worktree", "remove", "--force", str(place)], primary) in shell.commands


def stop_after_two_naps():
    naps = []

    def nap(seconds):
        naps.append(seconds)
        if len(naps) == 2:
            raise KeyboardInterrupt

    return naps, nap


def test_main_is_checked_and_the_outcome_logged_each_time_the_runner_wakes(tmp_path, caplog):
    bench = FakeBench("c3", ["a1", "b2", "c3"])
    ledger = a_ledger(tmp_path, verified="a1")
    naps, nap = stop_after_two_naps()
    with caplog.at_level("INFO"), pytest.raises(KeyboardInterrupt):
        main_verifier.keep_verifying(ledger, bench, nap=nap)

    assert naps == [main_verifier.POLL_S] * 2
    assert [record.getMessage() for record in caplog.records] == ["main at c3: passed", "main at c3: nothing"]


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

    def suite(command, cwd, log, environment=None):
        runs.append(command[3:])
        with patch.dict(os.environ, environment, clear=True):
            data_file = coverage.Coverage(config_file=environment["COVERAGE_PROCESS_START"]).config.data_file
        data = coverage.CoverageData(basename=data_file, suffix=True)
        ran, only_found = ("x.py", "y.py") if command[3].endswith("test_a.py") else ("y.py", "x.py")
        data.add_lines({str(checkout / "fun_time" / ran): [1]})
        data.touch_files([str(checkout / "fun_time" / only_found)])
        data.write()
        log.write_text("", encoding="utf-8")
        return 0

    main_verifier.MachineBench(primary=primary, shell=shell, suite=suite).measure_coverage("c3")

    assert runs == [["tests/integration/test_a.py", "--no-cov"], ["tests/integration/test_b.py", "--no-cov"]]
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


def test_only_the_libraries_main_pins_newer_than_this_machine_has_are_installed(tmp_path):
    pyproject = ('    "app-support @ git+https://github.com/haglio/app_support@v0.1.182",\n'
                 '    "player-core @ git+https://github.com/haglio/player_core@v0.1.410",\n'
                 '    "shared-ui @ git+https://github.com/haglio/shared_ui@v0.1.159",\n')
    shell = FakeShell({"c3:pyproject.toml": pyproject})
    have = {"app-support": "0.1.183", "player-core": "0.1.408"}
    primary = tmp_path / "fun_time"

    main_verifier.MachineBench(primary=primary, shell=shell, installed=have.get).bring_the_libraries_up_to("c3")

    installs = [command for command, _ in shell.commands if command[1:4] == ["-m", "pip", "install"]]
    assert installs == [[str(primary / ".venv" / "Scripts" / "python.exe"), "-m", "pip", "install",
                         "player-core @ git+https://github.com/haglio/player_core@v0.1.410",
                         "shared-ui @ git+https://github.com/haglio/shared_ui@v0.1.159"]]


def test_a_fix_session_starts_in_a_checkout_of_its_own_and_is_told_what_broke(tmp_path):
    shell = FakeShell({"auth status": '{"loggedIn": true}',
                       "--bg": "backgrounded \xb7 812181fb \xb7 Fix #41: Loop the row on the main player\n"})
    primary = tmp_path / "fun_time"
    bench = main_verifier.MachineBench(primary=primary, shell=shell, claude="claude.exe")
    taken = main_verifier.TakeBack(41, "Loop the row on the main player", 77)

    started = bench.start_fix_session(taken, ["tests/integration/test_x.py::test_y"], Path("C:/runs/d4.txt"))

    assert started == "812181fb"
    place = primary / ".claude" / "worktrees" / "fix-41"
    assert (["git", "worktree", "add", "-B", "claude/fix-41", str(place), "origin/main"], primary) in shell.commands
    assert (place / "state" / "genau_project_dirs.txt").read_text(encoding="utf-8") == ""
    command, cwd = next((command, cwd) for command, cwd in shell.commands if "--bg" in command)
    assert command[0] == "claude.exe" and cwd == place
    assert command[command.index("--permission-mode") + 1] == "auto"
    assert command[command.index("--name") + 1] == "Fix #41: Loop the row on the main player"
    brief = command[-1]
    for detail in ("#41", "Loop the row on the main player", "tests/integration/test_x.py::test_y", "#77",
                   str(Path("C:/runs/d4.txt")), "claude/fix-41"):
        assert detail in brief


def test_no_fix_session_starts_while_claude_code_is_not_signed_in(tmp_path):
    class SignedOutShell(FakeShell):
        def __call__(self, command, cwd=None):
            if command[1:] == ["auth", "status"]:
                raise main_verifier.CommandFailed('claude.exe auth status: {"loggedIn": false}')
            return super().__call__(command, cwd)

    shell = SignedOutShell({})
    bench = main_verifier.MachineBench(primary=tmp_path / "fun_time", shell=shell, claude="claude.exe")

    with pytest.raises(main_verifier.CommandFailed, match="not signed in"):
        bench.start_fix_session(main_verifier.TakeBack(41, "Loop the row on the main player", 77), [], Path("run.txt"))

    assert not [command for command, _ in shell.commands if command[:3] == ["git", "worktree", "add"]]


def test_fix_sessions_run_on_the_newest_claude_code_the_claude_app_keeps(tmp_path, monkeypatch):
    kept = tmp_path / "Packages" / "Claude_pzs8sxrjxfjjc" / "LocalCache" / "Roaming" / "Claude" / "claude-code"
    for version, folder in (("2.1.289", "e1f0154146bb"), ("2.1.293", "83cb0bd7fed4"), ("2.1.30", "0c0ffee0")):
        (kept / version / folder).mkdir(parents=True)
        (kept / version / folder / "claude.exe").write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert main_verifier.claude_command() == str(kept / "2.1.293" / "83cb0bd7fed4" / "claude.exe")
