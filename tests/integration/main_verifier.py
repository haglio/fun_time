from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

import coverage
import pytest
from app_support.file_channel import write_whole
from app_support.logging_utils import configure_logging, install_exception_logging
from app_support.subprocess_utils import hidden_subprocess_kwargs

from fun_time.checkout_overrides import GENAU_DIRS_OVERRIDE_NAME, STATE_DIRNAME, primary_of

from .coverage_map import CoverageMap, source_files, the_machine_s_map

REPO = "haglio/fun_time"
POLL_S = 300
COVERAGE_MAP_LASTS = timedelta(days=1)
NO_SUCH_TEST = pytest.ExitCode.USAGE_ERROR
_log = logging.getLogger("main_verifier")
_SUMMARY_LINE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)


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


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    failed: list[str]
    log: Path


class Bench(Protocol):
    def head(self) -> str: ...
    def commits_since(self, base: str | None, head: str) -> list[str]: ...
    def run(self, commit: str, tests: Sequence[str] = ()) -> RunResult: ...
    def advance_everyday_checkout(self, commit: str) -> None: ...
    def take_back(self, commit: str, among: Sequence[str], tests: list[str]) -> int | None: ...
    def record(self, incident: dict) -> None: ...
    def coverage_map_is_stale(self) -> bool: ...
    def measure_coverage(self, commit: str) -> None: ...


class CommandFailed(Exception):
    pass


def _shell(command: Sequence[str], cwd: Path | None = None) -> str:
    done = subprocess.run(list(command), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **hidden_subprocess_kwargs())
    if done.returncode:
        raise CommandFailed(f"{' '.join(command)}: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def _run_suite(command: Sequence[str], cwd: Path, log: Path, environment: Mapping[str, str] | None = None) -> int:
    with log.open("w", encoding="utf-8") as sink:
        return subprocess.run(list(command), cwd=cwd, stdout=sink, stderr=subprocess.STDOUT, env=environment,
                              **hidden_subprocess_kwargs()).returncode


def _measured_sources(data_file: Path, checkout: Path, sources: frozenset[str]) -> frozenset[str]:
    combined = coverage.Coverage(data_file=str(data_file), config_file=False)
    combined.combine(data_paths=[str(data_file.parent)])
    data = combined.get_data()
    by_path = {os.path.normcase(str(checkout / source)): source for source in sources}
    return frozenset(by_path[os.path.normcase(measured)] for measured in data.measured_files()
                     if data.lines(measured) and os.path.normcase(measured) in by_path)


class MachineBench:
    def __init__(self, primary: Path, shell=_shell, suite=_run_suite) -> None:
        self.primary, self.shell, self.suite = primary, shell, suite
        self.checkout = primary / ".claude" / "worktrees" / "verifying-main"
        self.state = primary / STATE_DIRNAME

    def head(self) -> str:
        self.shell(["git", "fetch", "origin", "main"], cwd=self.primary)
        return self.shell(["git", "rev-parse", "origin/main"], cwd=self.primary).strip()

    def run(self, commit: str, tests: Sequence[str] = ()) -> RunResult:
        self._check_out(commit)
        log = self._log_for(commit)
        exit_code = self.suite(self._runner(*tests), self.checkout, log)
        return RunResult(exit_code, failed_tests(log.read_text(encoding="utf-8", errors="replace")), log)

    def coverage_map_is_stale(self) -> bool:
        try:
            age_s = time.time() - the_machine_s_map(self.primary).stat().st_mtime
        except OSError:
            return True
        return age_s >= COVERAGE_MAP_LASTS.total_seconds()

    def measure_coverage(self, commit: str) -> None:
        self._check_out(commit)
        sources = source_files(self.shell(["git", "ls-files", "--", "*.py"], cwd=self.checkout).split())
        measuring = self.state / "main_verifier_coverage"
        measuring.mkdir(parents=True, exist_ok=True)
        rcfile = measuring / "coveragerc"
        rcfile.write_text("\n".join(["[run]", f"source = {self.checkout}", f"omit = {self.checkout / 'tests'}/*",
                                      f"data_file = {measuring / '.coverage'}", "parallel = true", ""]),
                          encoding="utf-8")
        measuring_everything = {**os.environ, "COVERAGE_PROCESS_START": str(rcfile)}
        ran = {}
        for test_file in sorted(path for path in self.shell(["git", "ls-files", "--", "tests/integration/test_*.py"],
                                                             cwd=self.checkout).split()):
            for left_over in measuring.glob(".coverage*"):
                left_over.unlink()
            self.suite(self._runner(test_file, "--no-cov"), self.checkout, self._log_for(commit), measuring_everything)
            ran[test_file] = _measured_sources(measuring / ".coverage", self.checkout, sources)
        CoverageMap(commit, ran).save(the_machine_s_map(self.primary))

    def _runner(self, *arguments: str) -> list[str]:
        return [str(self.primary / ".venv" / "Scripts" / "python.exe"), "-m", "tests.integration.hidden_desktop",
                *arguments]

    def _log_for(self, commit: str) -> Path:
        log = self.state / "main_verifier_runs" / f"{datetime.now():%Y%m%d-%H%M%S}-{commit[:10]}.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        return log

    def _check_out(self, commit: str) -> None:
        if self.checkout.exists():
            self.shell(["git", "checkout", "--detach", "--force", commit], cwd=self.checkout)
        else:
            self.shell(["git", "worktree", "add", "--detach", str(self.checkout), commit], cwd=self.primary)
        overrides = self.checkout / STATE_DIRNAME
        overrides.mkdir(parents=True, exist_ok=True)
        (overrides / GENAU_DIRS_OVERRIDE_NAME).write_text("", encoding="utf-8")

    def advance_everyday_checkout(self, commit: str) -> None:
        if self.shell(["git", "status", "--porcelain"], cwd=self.primary).strip():
            raise CommandFailed("the everyday checkout has uncommitted changes, so it was left where it is")
        if self.shell(["git", "symbolic-ref", "--short", "HEAD"], cwd=self.primary).strip() != "main":
            raise CommandFailed("the everyday checkout is not on main, so it was left where it is")
        self.shell(["git", "merge", "--ff-only", commit], cwd=self.primary)

    def commits_since(self, base: str | None, head: str) -> list[str]:
        span = [f"{base}..{head}"] if base else ["--max-count=20", head]
        return self.shell(["git", "rev-list", "--first-parent", "--reverse", *span], cwd=self.primary).split()

    def _pull_request_of(self, commit: str) -> int | None:
        answer = self.shell(["gh", "api", f"repos/{REPO}/commits/{commit}/pulls", "--jq", ".[0].number"]).strip()
        return int(answer) if answer.isdigit() else None

    def take_back(self, commit: str, among: Sequence[str], tests: list[str]) -> int | None:
        number = self._pull_request_of(commit)
        if number is None:
            return None
        its_commits = [each for each in among if self._pull_request_of(each) == number]
        title = self.shell(["gh", "pr", "view", str(number), "--repo", REPO, "--json", "title",
                            "--jq", ".title"]).strip()
        branch = f"claude/take-back-{number}"
        place = self.primary / ".claude" / "worktrees" / f"take-back-{number}"
        body = (f"#{number} failed the full integration suite on main, which these tests passed before it:\n\n"
                + "\n".join(f"- `{test}`" for test in tests)
                + f"\n\nThis takes #{number} back out of main until it is fixed and shipped again.")
        try:
            self.shell(["git", "worktree", "add", "-B", branch, str(place), "origin/main"], cwd=self.primary)
            try:
                self.shell(["git", "revert", "--no-edit", *reversed(its_commits)], cwd=place)
                self.shell(["git", "push", "--force", "-u", "origin", branch], cwd=place)
            finally:
                self.shell(["git", "worktree", "remove", "--force", str(place)], cwd=self.primary)
            url = self.shell(["gh", "pr", "create", "--repo", REPO, "--head", branch, "--base", "main",
                              "--title", f"Take back #{number}: {title}", "--body", body])
        except CommandFailed:
            return None
        return int(url.strip().rsplit("/", 1)[-1])

    def record(self, incident: dict) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / "main_verifier_incidents.jsonl").open("a", encoding="utf-8") as sink:
            sink.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), **incident}, default=str) + "\n")


def _mark_passed(ledger: Ledger, bench: Bench, head: str) -> None:
    ledger.passed(head)
    try:
        bench.advance_everyday_checkout(head)
    except CommandFailed as held:
        bench.record({"kind": "everyday checkout held", "commit": head, "why": str(held)})
    if bench.coverage_map_is_stale():
        bench.measure_coverage(head)


def keep_verifying(ledger: Ledger, bench: Bench, nap: Callable[[float], None] = time.sleep) -> None:
    while True:
        try:
            cycle(ledger, bench)
        except CommandFailed as error:
            _log.warning("could not check main: %s", error)
        nap(POLL_S)


def cycle(ledger: Ledger, bench: Bench) -> str:
    head = bench.head()
    outcome = next_step(ledger, head)
    if outcome == "verify":
        outcome = _verify(ledger, bench, head)
    _log.info("main at %s: %s", head, outcome)
    return outcome


def _verify(ledger: Ledger, bench: Bench, head: str) -> str:
    whole = bench.run(head)
    if whole.exit_code == 0:
        _mark_passed(ledger, bench, head)
        return "passed"
    again = bench.run(head, whole.failed).exit_code if whole.failed else NO_SUCH_TEST
    if again == NO_SUCH_TEST:
        bench.record({"kind": "broken run", "commit": head, "log": whole.log})
        ledger.failed(head)
        return "broken run"
    if again == 0:
        bench.record({"kind": "flaky", "commit": head, "tests": whole.failed, "log": whole.log})
        _mark_passed(ledger, bench, head)
        return "flaky"
    shipped = bench.commits_since(ledger.verified, head)
    culprit = first_bad(shipped, lambda commit: bench.run(commit, whole.failed).exit_code not in (0, NO_SUCH_TEST))
    bench.record({"kind": "broke main", "commit": culprit, "tests": whole.failed, "log": whole.log,
                  "taken_back_by": bench.take_back(culprit, shipped, whole.failed)})
    ledger.failed(head)
    return "broke main"


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
    install_exception_logging(configure_logging("main_verifier", primary / STATE_DIRNAME / "main_verifier.log"))
    keep_verifying(Ledger(primary / STATE_DIRNAME / "main_verifier.json"), MachineBench(primary))


if __name__ == "__main__":
    main()
