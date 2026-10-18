from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Protocol

import coverage
import pytest
from app_support.file_channel import write_whole
from app_support.logging_utils import configure_logging, install_exception_logging
from app_support.subprocess_utils import hidden_subprocess_kwargs

from fun_time.checkout_overrides import primary_of
from fun_time.process_tree import kill_process_tree
from fun_time.project_paths import SIBLING_CHECKOUTS_OVERRIDE_NAME, STATE_DIRNAME

from .commands import CommandFailed, run_command
from .coverage_map import CoverageMap, source_files, the_machine_s_map
from .fix_sessions import background_sessions, look_after_fix_sessions, shipped_again
from .session_lock import SingleInstanceLock

REPO = "haglio/fun_time"
ONE_COPY = r"Global\fun_time_main_verifier"
POLL_S = 300
RESTART_REQUEST_NAME = "main_verifier.restart"
INCIDENTS_NAME = "main_verifier_incidents.jsonl"
VERIFIER_VENV = "main_verifier_venv"
INSTALLED_FROM = "installed_from_pyproject.toml"
_ASKED_EVERY_S = 5
COVERAGE_MAP_LASTS = timedelta(days=1)
CONFIRMING_RUNS = 3
NOT_AT_THAT_COMMIT = pytest.ExitCode.NO_TESTS_COLLECTED
_PASSED = frozenset({"passed", "flaky"})
_STILL_FIXING = frozenset({"working", "blocked", "failed"})
_FIXED_BY_A_SESSION_OF_ITS_OWN = frozenset({"unreliable check", "broken outside main"})
_log = logging.getLogger("main_verifier")
_SUMMARY_LINE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)
_BACKGROUNDED = re.compile(r"backgrounded\W+([0-9a-f]{8})\b")


class Ledger:
    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        self.verified: str | None = saved.get("verified")
        self.failed_head: str | None = saved.get("failed_head")

    def passed(self, commit: str) -> None:
        self.verified, self.failed_head = commit, None
        self._save()

    def failed(self, commit: str) -> None:
        self.failed_head = commit
        self._save()

    def _save(self) -> None:
        write_whole(self._path, json.dumps({"verified": self.verified, "failed_head": self.failed_head}, indent=1))


def next_step(ledger: Ledger, head: str) -> str:
    if head == ledger.verified:
        return "nothing"
    if head == ledger.failed_head:
        return "wait"
    return "verify"


class Verdict(Enum):
    FAILS = "fails"
    PASSES = "passes"
    NONE = "no verdict"


_VERDICTS = {pytest.ExitCode.TESTS_FAILED: Verdict.FAILS, pytest.ExitCode.OK: Verdict.PASSES,
             NOT_AT_THAT_COMMIT: Verdict.PASSES}


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    failed: list[str]
    log: Path

    @property
    def verdict(self) -> Verdict:
        return _VERDICTS.get(self.exit_code, Verdict.NONE)


@dataclass(frozen=True)
class TakeBack:
    pull_request: int
    title: str
    by: int


@dataclass(frozen=True)
class FixSession:
    checkout: str
    title: str
    brief: str


class Bench(Protocol):
    def head(self) -> str: ...
    def commits_since(self, base: str | None, head: str) -> list[str]: ...
    def run(self, commit: str, tests: Sequence[str] = ()) -> RunResult: ...
    def release_to_production(self, commit: str) -> None: ...
    def pull_request_of(self, commit: str) -> int | None: ...
    def take_back(self, commit: str, among: Sequence[str], tests: list[str]) -> TakeBack | None: ...
    def ship_again(self, pull_request: int, take_back: Sequence[str], tests: list[str]) -> int | None: ...
    def start_fix_session(self, fix: FixSession) -> str: ...
    def stop_fix_session(self, session: str) -> None: ...
    def fix_session_states(self) -> dict[str, str]: ...
    def record(self, incident: dict) -> None: ...
    def incidents(self) -> list[dict]: ...
    def coverage_map_is_stale(self) -> bool: ...
    def measure_coverage(self, commit: str) -> None: ...
    def take_restart_request(self) -> bool: ...


class RestartRequested(Exception):
    pass


def _run_suite(command: Sequence[str], cwd: Path, log: Path, environment: Mapping[str, str] | None = None, *,
               asked_to_restart: Callable[[], bool]) -> int:
    with log.open("w", encoding="utf-8") as sink:
        suite = subprocess.Popen(list(command), cwd=cwd, stdout=sink, stderr=subprocess.STDOUT, env=environment,
                                 **hidden_subprocess_kwargs())
        while True:
            try:
                return suite.wait(timeout=_ASKED_EVERY_S)
            except subprocess.TimeoutExpired:
                if asked_to_restart():
                    kill_process_tree(suite.pid)
                    raise RestartRequested from None


def measured_run_environment(rcfile: Path) -> dict[str, str]:
    return {**{name: value for name, value in os.environ.items() if name != "COVERAGE_FILE"},
            "COVERAGE_PROCESS_START": str(rcfile)}


def measured_sources(data_file: Path, checkout: Path, sources: frozenset[str]) -> frozenset[str]:
    by_path = {os.path.normcase(str(checkout / source)): source for source in sources}
    ran: set[str] = set()
    for one_process in data_file.parent.glob(f"{data_file.name}.*"):
        try:
            ran |= _what_one_process_ran(one_process, by_path)
        except (coverage.CoverageException, sqlite3.Error) as torn:
            _log.warning("left %s out of the coverage map: %s", one_process.name, torn)
    return frozenset(ran)


def _what_one_process_ran(saved: Path, by_path: Mapping[str, str]) -> set[str]:
    data = coverage.CoverageData(str(saved))
    try:
        data.read()
        return {by_path[key] for measured in data.measured_files()
                if (key := os.path.normcase(measured)) in by_path and data.lines(measured)}
    finally:
        data.close()


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def claude_command() -> str:
    kept = Path(os.environ.get("LOCALAPPDATA", "")).glob(
        "Packages/Claude_*/LocalCache/Roaming/Claude/claude-code/*/*/claude.exe")
    newest = max(kept, key=lambda copy: _version(copy.parent.parent.name), default=None)
    return str(newest) if newest else shutil.which("claude") or "claude"


def branch_of(checkout: str) -> str:
    return f"claude/{checkout}"


def _listed(tests: list[str]) -> str:
    return "\n".join(f"- `{test}`" for test in tests)


def _use_the_installed_siblings(checkout: Path) -> None:
    overrides = checkout / STATE_DIRNAME
    overrides.mkdir(parents=True, exist_ok=True)
    (overrides / SIBLING_CHECKOUTS_OVERRIDE_NAME).write_text("", encoding="utf-8")


class MachineBench:
    def __init__(self, primary: Path, shell=run_command, suite=_run_suite, claude: str | None = None,
                 base_python: str = str(Path(sys._base_executable).with_name("python.exe"))) -> None:
        self.primary, self.shell, self.suite, self.base_python = primary, shell, suite, base_python
        self.claude = claude or claude_command()
        self.checkout = primary / ".claude" / "worktrees" / "verifying-main"
        self.state = primary / STATE_DIRNAME
        self.venv = self.state / VERIFIER_VENV
        self.python = self.venv / "Scripts" / "python.exe"
        self.restart_request = self.state / RESTART_REQUEST_NAME

    def asked_to_restart(self) -> bool:
        return self.restart_request.exists()

    def take_restart_request(self) -> bool:
        try:
            self.restart_request.unlink()
        except FileNotFoundError:
            return False
        return True

    def install_for(self, commit: str) -> None:
        self._check_out(commit)
        pyproject = self.shell(["git", "show", f"{commit}:pyproject.toml"], cwd=self.primary)
        if not self.python.exists():
            self.shell([self.base_python, "-m", "venv", str(self.venv)], cwd=self.primary)
        installed_from = self.venv / INSTALLED_FROM
        if not installed_from.is_file() or installed_from.read_text(encoding="utf-8") != pyproject:
            extras = ",".join(sorted(tomllib.loads(pyproject).get("project", {}).get("optional-dependencies", {})))
            self.shell([str(self.python), "-m", "pip", "install", "-e", f"{self.checkout}[{extras}]",
                        "--config-settings", "editable_mode=compat"], cwd=self.primary)
            write_whole(installed_from, pyproject)

    def head(self) -> str:
        self.shell(["git", "fetch", "origin", "main"], cwd=self.primary)
        return self.shell(["git", "rev-parse", "origin/main"], cwd=self.primary).strip()

    def run(self, commit: str, tests: Sequence[str] = ()) -> RunResult:
        there = [test for test in tests if self._has(commit, test)]
        log = self._log_for(commit)
        if tests and not there:
            log.write_text(f"none of these tests is at {commit} yet:\n{_listed(list(tests))}\n", encoding="utf-8")
            return RunResult(NOT_AT_THAT_COMMIT, [], log)
        self.install_for(commit)
        exit_code = self.suite(self._runner(*there), self.checkout, log, asked_to_restart=self.asked_to_restart)
        return RunResult(exit_code, failed_tests(log.read_text(encoding="utf-8", errors="replace")), log)

    def _has(self, commit: str, test: str) -> bool:
        path, _, inside = test.partition("::")
        try:
            source = self.shell(["git", "show", f"{commit}:{path}"], cwd=self.primary)
        except CommandFailed:
            return False
        names = [name.partition("[")[0] for name in inside.split("::") if name]
        return all(re.search(rf"\b{re.escape(name)}\b", source) for name in names)

    def coverage_map_is_stale(self) -> bool:
        try:
            age_s = time.time() - the_machine_s_map(self.primary).stat().st_mtime
        except OSError:
            return True
        return age_s >= COVERAGE_MAP_LASTS.total_seconds()

    def measure_coverage(self, commit: str) -> None:
        self.install_for(commit)
        sources = source_files(self.shell(["git", "ls-files", "--", "*.py"], cwd=self.checkout).split())
        measuring = self.state / "main_verifier_coverage"
        measuring.mkdir(parents=True, exist_ok=True)
        rcfile = measuring / "coveragerc"
        rcfile.write_text("\n".join(["[run]", f"source = {self.checkout}", f"omit = {self.checkout / 'tests'}/*",
                                      f"data_file = {measuring / '.coverage'}", "parallel = true", ""]),
                          encoding="utf-8")
        ran = {}
        for test_file in sorted(path for path in self.shell(["git", "ls-files", "--", "tests/integration/test_*.py"],
                                                             cwd=self.checkout).split()):
            for left_over in measuring.glob(".coverage*"):
                left_over.unlink()
            self.suite(self._runner(test_file, "--no-cov"), self.checkout, self._log_for(commit),
                       measured_run_environment(rcfile), asked_to_restart=self.asked_to_restart)
            ran[test_file] = measured_sources(measuring / ".coverage", self.checkout, sources)
        CoverageMap(commit, ran).save(the_machine_s_map(self.primary))

    def _runner(self, *arguments: str) -> list[str]:
        return [str(self.python), "-m", "tests.integration.hidden_desktop", *arguments]

    def _log_for(self, commit: str) -> Path:
        log = self.state / "main_verifier_runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{commit[:10]}.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        return log

    def _check_out(self, commit: str) -> None:
        if self.checkout.exists():
            self.shell(["git", "checkout", "--detach", "--force", commit], cwd=self.checkout)
        else:
            self.shell(["git", "worktree", "add", "--detach", str(self.checkout), commit], cwd=self.primary)
        _use_the_installed_siblings(self.checkout)

    def release_to_production(self, commit: str) -> None:
        declared = tomllib.loads((self.primary / "pyproject.toml").read_text(encoding="utf-8"))
        write_whole(self.primary / declared["tool"]["haglio"]["production"]["commit-file"], commit)

    def commits_since(self, base: str | None, head: str) -> list[str]:
        span = [f"{base}..{head}"] if base else ["--max-count=20", head]
        return self.shell(["git", "rev-list", "--first-parent", "--reverse", *span], cwd=self.primary).split()

    def pull_request_of(self, commit: str) -> int | None:
        answer = self.shell(["gh", "api", f"repos/{REPO}/commits/{commit}/pulls", "--jq", ".[0].number"]).strip()
        return int(answer) if answer.isdigit() else None

    def _title_of(self, pull_request: int) -> str:
        return self.shell(["gh", "pr", "view", str(pull_request), "--repo", REPO, "--json", "title",
                           "--jq", ".title"]).strip()

    def take_back(self, commit: str, among: Sequence[str], tests: list[str]) -> TakeBack | None:
        number = self.pull_request_of(commit)
        if number is None:
            return None
        its_commits = [each for each in among if self.pull_request_of(each) == number]
        title = self._title_of(number)
        body = (f"#{number} failed the full integration suite on main, which these tests passed before it:\n\n"
                + _listed(tests) + f"\n\nThis takes #{number} back out of main until it is fixed and shipped again.")
        by = self._revert_in_a_pull_request(its_commits, f"take-back-{number}", f"Take back #{number}: {title}", body)
        return TakeBack(number, title, by) if by else None

    def ship_again(self, pull_request: int, take_back: Sequence[str], tests: list[str]) -> int | None:
        body = (f"#{pull_request} was taken back out of main for failing these tests, and they failed again on "
                f"main without it, so it was not what failed them:\n\n" + _listed(tests)
                + f"\n\nThis ships #{pull_request} again.")
        return self._revert_in_a_pull_request(take_back, f"ship-again-{pull_request}",
                                              f"Ship #{pull_request} again: {self._title_of(pull_request)}", body)

    def _revert_in_a_pull_request(self, commits: Sequence[str], checkout: str, title: str, body: str) -> int | None:
        branch, place = branch_of(checkout), self.primary / ".claude" / "worktrees" / checkout
        try:
            self.shell(["git", "worktree", "add", "-B", branch, str(place), "origin/main"], cwd=self.primary)
            try:
                self.shell(["git", "revert", "--no-edit", *reversed(commits)], cwd=place)
                self.shell(["git", "push", "--force", "-u", "origin", branch], cwd=place)
            finally:
                self.shell(["git", "worktree", "remove", "--force", str(place)], cwd=self.primary)
            url = self.shell(["gh", "pr", "create", "--repo", REPO, "--head", branch, "--base", "main",
                              "--title", title, "--body", body])
        except CommandFailed:
            return None
        return int(url.strip().rsplit("/", 1)[-1])

    def start_fix_session(self, fix: FixSession) -> str:
        try:
            self.shell([self.claude, "auth", "status"])
        except CommandFailed:
            raise CommandFailed("the Claude command line is not signed in") from None
        place = self.primary / ".claude" / "worktrees" / fix.checkout
        self.shell(["git", "worktree", "add", "-B", branch_of(fix.checkout), str(place), "origin/main"],
                   cwd=self.primary)
        _use_the_installed_siblings(place)
        answer = self.shell([self.claude, "--bg", "--name", fix.title, "--permission-mode", "auto", fix.brief],
                            cwd=place)
        started = _BACKGROUNDED.search(answer)
        if started is None:
            raise CommandFailed(f"the Claude command line did not say it started the session: {answer.strip()}")
        return started.group(1)

    def stop_fix_session(self, session: str) -> None:
        self.shell([self.claude, "stop", session])

    def record(self, incident: dict) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / INCIDENTS_NAME).open("a", encoding="utf-8") as sink:
            sink.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **incident}, default=str) + "\n")

    def incidents(self) -> list[dict]:
        return _incidents(self.state)

    def fix_session_states(self) -> dict[str, str]:
        return {session["id"]: session.get("state") for session in background_sessions(self.claude, self.shell)
                if "id" in session}


def _what_failed(tests: list[str]) -> list[str]:
    return ["Fun Time's main verifier started this session. It is a program on this machine, not him, so report "
            "the way the laws ask of work another agent assigned you.",
            "",
            "The verifier ran Fun Time's whole integration suite on main, and these tests failed:",
            *(f"- {test}" for test in tests)]


def fixing_a_take_back(taken: TakeBack, tests: list[str], log: Path) -> FixSession:
    number, by = taken.pull_request, taken.by
    checkout = f"fix-{number}"
    branch = branch_of(checkout)
    return FixSession(checkout, f"Fix #{number}: {taken.title}", "\n".join([
        *_what_failed(tests),
        f'It traced the failure to #{number} ("{taken.title}") and opened #{by}, which takes #{number} back out '
        f"of main. The failing run's output is in {log}.",
        "",
        f"Ship #{number}'s change again, without the failure:",
        f"1. Wait for #{by} to ship (gh pr view {by} --repo {REPO} --json state). If it is closed without "
        "shipping, stop and say so.",
        f"2. Rebase this branch, {branch}, onto origin/main, then revert #{by}'s commit, which brings "
        f"#{number}'s change back.",
        f"3. Find why those tests fail with #{number}'s change in, and fix the cause. A test that turns out to "
        "be wrong is fixed like any other code, never deleted or loosened to pass.",
        "4. Run the unit suite, the integration tests this branch needs "
        "(python -m tests.integration.hidden_desktop --changed) and the tests above by name, until every one "
        "passes.",
        f"5. He accepted #{number}'s change before it shipped. If your fix changes nothing he would see "
        f"compared with #{number}, ship it the way this repo's CLAUDE.md says, without asking him. If it does "
        "change something he would see, do not ship it: open it as a draft pull request, say in your reply "
        "what he would see differently, and stop.",
    ]))


def fixing_unreliable_checks(head: str, tests: list[str], log: Path, why: str) -> FixSession:
    checkout = f"fix-unreliable-{head[:8]}"
    files = sorted({PurePosixPath(test.split("::")[0]).stem for test in tests})
    return FixSession(checkout, f"Fix the unreliable {' and '.join(files)}", "\n".join([
        *_what_failed(tests),
        f"{why} The failing run's output is in {log}, and each later run of these tests is beside it.",
        "",
        "Make them pass every time:",
        f"1. Find why they fail only some of the time, and fix the cause on this branch, {branch_of(checkout)}. "
        "A test that turns out to be wrong is fixed like any other code, never deleted, skipped or loosened "
        "to pass.",
        "2. Run the unit suite, the integration tests this branch needs "
        "(python -m tests.integration.hidden_desktop --changed) and the tests above by name, several times, "
        "until every run passes.",
        "3. If your fix changes nothing he would see, ship it the way this repo's CLAUDE.md says, without "
        "asking him. If it does change something he would see, do not ship it: open it as a draft pull "
        "request, say in your reply what he would see differently, and stop.",
    ]))


def fixing_an_outside_break(head: str, last_passed: str, tests: list[str], log: Path) -> FixSession:
    checkout = f"fix-outside-{head[:8]}"
    files = sorted({PurePosixPath(test.split("::")[0]).stem for test in tests})
    return FixSession(checkout, f"Find what outside Fun Time broke {' and '.join(files)}", "\n".join([
        *_what_failed(tests),
        f"They fail the same way on {last_passed}, the last commit of main that passed, so no change shipped "
        "since then is what failed them: something outside Fun Time's own code changed under them, such as "
        "the Origenerator a session hosts, a library this computer has installed, the settings Fun Time or "
        f"Origenerator read, or the machine itself. The failing run's output is in {log}, and the run on "
        f"{last_passed} is beside it. The verifier took nothing back for them.",
        "",
        "Find what changed and put it right where it lives:",
        "1. Read both failing runs, then compare what those tests reach outside Fun Time now with what it was "
        f"when {last_passed} passed.",
        "2. Where the cause is another repo's change, fix it in that repo, the way its CLAUDE.md says, and ship "
        f"it. Where it is Fun Time's own code after all, fix it on this branch, {branch_of(checkout)}.",
        "3. Where only he can put it right, such as a key his own settings file does not have yet, never change "
        "his files yourself: say in your reply exactly what he must do, in plain words, and stop.",
        "4. Main is tried again the next time a change ships, and the tests above have to pass there.",
    ]))


def _incidents(state: Path) -> list[dict]:
    try:
        lines = (state / INCIDENTS_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    incidents = []
    for line in lines:
        with contextlib.suppress(ValueError):
            incidents.append(json.loads(line))
    return incidents


def _mark_passed(ledger: Ledger, bench: Bench, head: str) -> None:
    ledger.passed(head)
    try:
        bench.release_to_production(head)
    except OSError as held:
        bench.record({"kind": "release not written", "commit": head, "why": str(held)})
    if bench.coverage_map_is_stale():
        bench.measure_coverage(head)


def keep_verifying(ledger: Ledger, bench: Bench, nap: Callable[[float], None] = time.sleep) -> None:
    while not bench.take_restart_request():
        try:
            if cycle(ledger, bench) in _PASSED:
                return
        except CommandFailed as error:
            _log.warning("could not check main: %s", error)
        except RestartRequested:
            continue
        nap(POLL_S)
    _log.info("restarting, as asked")


def cycle(ledger: Ledger, bench: Bench) -> str:
    head = bench.head()
    outcome = next_step(ledger, head)
    if outcome == "verify":
        outcome = _verify(ledger, bench, head)
    _log.info("main at %s: %s", head, outcome)
    return outcome


class _NoVerdict(Exception):
    def __init__(self, commit: str, run: RunResult) -> None:
        super().__init__(commit)
        self.commit, self.run = commit, run


def _verify(ledger: Ledger, bench: Bench, head: str) -> str:
    whole = bench.run(head)
    if whole.exit_code == 0:
        _mark_passed(ledger, bench, head)
        return "passed"
    again = bench.run(head, whole.failed).verdict if whole.failed else Verdict.NONE
    if again is Verdict.NONE:
        bench.record({"kind": "broken run", "commit": head, "log": whole.log})
        ledger.failed(head)
        return "broken run"
    if again is Verdict.PASSES:
        bench.record({"kind": "flaky", "commit": head, "tests": whole.failed, "log": whole.log})
        _mark_passed(ledger, bench, head)
        return "flaky"
    try:
        outcome = _blame(bench, ledger.verified, head, whole)
    except _NoVerdict as unjudged:
        bench.record({"kind": "could not judge", "commit": head, "no_verdict_on": unjudged.commit,
                      "tests": whole.failed, "log": unjudged.run.log})
        outcome = "could not judge"
    ledger.failed(head)
    return outcome


def _blame(bench: Bench, last_passed: str | None, head: str, whole: RunResult) -> str:
    def still_fails(commit: str) -> bool:
        run = bench.run(commit, whole.failed)
        if run.verdict is Verdict.NONE:
            raise _NoVerdict(commit, run)
        return run.verdict is Verdict.FAILS

    shipped = bench.commits_since(last_passed, head)
    if wrongly := _taken_back_wrongly(bench, whole.failed, shipped):
        return _ship_again(bench, head, whole, *wrongly)
    if last_passed and still_fails(last_passed):
        _record_with_a_fix_session(
            bench, {"kind": "broken outside main", "commit": head, "last_passed": last_passed,
                    "tests": whole.failed, "log": whole.log},
            lambda: fixing_an_outside_break(head, last_passed, whole.failed, whole.log))
        return "broken outside main"
    culprit = first_bad(shipped, still_fails)
    without = [last_passed, *shipped][shipped.index(culprit)]
    if all(still_fails(culprit) and (without is None or not still_fails(without)) for _ in range(CONFIRMING_RUNS)):
        return _take_back(bench, culprit, shipped, whole)
    return _unreliable(bench, head, whole, {"suspected": culprit},
                       "Run again by themselves, they passed on some runs and failed on others, so the verifier "
                       "could not pin them on any change and took nothing back.")


def _taken_back_wrongly(bench: Bench, failed: list[str], shipped: list[str]) -> tuple[dict, list[str]] | None:
    incidents = bench.incidents()
    changes_shipped_again = shipped_again(incidents)
    for incident in incidents:
        if (incident.get("taken_back_by") and incident.get("pull_request") not in changes_shipped_again
                and set(failed) <= set(incident.get("tests", ()))):
            take_back = [commit for commit in shipped if bench.pull_request_of(commit) == incident["taken_back_by"]]
            if take_back:
                return incident, take_back
    return None


def _ship_again(bench: Bench, head: str, whole: RunResult, taken_back: dict, take_back: list[str]) -> str:
    number, by = taken_back["pull_request"], taken_back["taken_back_by"]
    facts = {"pull_request": number, "shipped_again_by": bench.ship_again(number, take_back, whole.failed)}
    if facts["shipped_again_by"] and taken_back.get("fix_session"):
        try:
            bench.stop_fix_session(taken_back["fix_session"])
        except CommandFailed as why:
            facts["fix_session_not_stopped"] = str(why)
    return _unreliable(bench, head, whole, facts,
                       f"#{by} had taken #{number} back out of main for failing them, and they failed again on "
                       f"main without #{number}, so #{number} was not what failed them.")


def _take_back(bench: Bench, culprit: str, shipped: list[str], whole: RunResult) -> str:
    taken = bench.take_back(culprit, shipped, whole.failed)
    incident = {"kind": "broke main", "commit": culprit, "tests": whole.failed, "log": whole.log,
                "taken_back_by": taken.by if taken else None}
    if taken:
        incident["pull_request"] = taken.pull_request
        try:
            incident["fix_session"] = bench.start_fix_session(fixing_a_take_back(taken, whole.failed, whole.log))
        except CommandFailed as why:
            incident["fix_session_not_started"] = str(why)
    bench.record(incident)
    return "broke main"


def _unreliable(bench: Bench, head: str, whole: RunResult, facts: dict, why: str) -> str:
    _record_with_a_fix_session(
        bench, {"kind": "unreliable check", "commit": head, "tests": whole.failed, "log": whole.log, **facts},
        lambda: fixing_unreliable_checks(head, whole.failed, whole.log, why))
    return "unreliable check"


def _record_with_a_fix_session(bench: Bench, incident: dict, fix: Callable[[], FixSession]) -> None:
    try:
        if fixing := _session_fixing(bench, incident["tests"]):
            incident["being_fixed_by"] = fixing
        else:
            incident["fix_session"] = bench.start_fix_session(fix())
    except CommandFailed as why_not:
        incident["fix_session_not_started"] = str(why_not)
    bench.record(incident)


def _session_fixing(bench: Bench, tests: list[str]) -> str | None:
    states = bench.fix_session_states()
    return next((incident["fix_session"] for incident in bench.incidents()
                 if incident.get("kind") in _FIXED_BY_A_SESSION_OF_ITS_OWN
                 and set(incident.get("tests", ())) & set(tests)
                 and states.get(incident.get("fix_session")) in _STILL_FIXING), None)


def failed_tests(run_output: str) -> list[str]:
    return sorted({test.replace("\\", "/") for test in _SUMMARY_LINE.findall(run_output)})


def first_bad(commits: Sequence[str], fails_at: Callable[[str], bool]) -> str:
    good, bad = -1, len(commits) - 1
    while bad - good > 1:
        middle = (good + bad) // 2
        if fails_at(commits[middle]):
            bad = middle
        else:
            good = middle
    return commits[bad]


def main() -> None:
    primary = primary_of(Path(__file__).resolve().parents[2])
    state = primary / STATE_DIRNAME
    install_exception_logging(configure_logging("main_verifier", state / "main_verifier.log"))
    try:
        look_after_fix_sessions(state, _incidents(state), claude_command())
    except (CommandFailed, OSError, ValueError) as why:
        _log.warning("could not check the fix sessions: %s", why)
    one_copy = SingleInstanceLock(ONE_COPY)
    if not one_copy.acquire(timeout=0):
        return
    keep_verifying(Ledger(state / "main_verifier.json"), MachineBench(primary))


if __name__ == "__main__":
    main()
