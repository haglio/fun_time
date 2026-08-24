"""The integration suite's committed run record.

The suite runs on the user's Windows machine, entered only through
``hidden_desktop``, and its result has always lived in whatever terminal
happened to be open.  Nothing in the repo said when the integration tests last
passed, or against which code — so a reader had no way to tell a suite green
last week from one green a year ago, and neither did an agent about to touch it.

The runner appends one line to ``docs/integration-runs.md`` as it tears a run
down.  Everything in that line is what a later reader needs to trust it: the UTC
time, the fun_time and ``player_core`` commits it ran, pass/fail with counts —
and, when the caller narrowed the run with extra pytest args, those args, so a
green ``-k nau`` can never be mistaken for a green suite.
"""
from __future__ import annotations

import importlib.util
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

LOG_RELATIVE_PATH = "docs/integration-runs.md"

# What a row says when a checkout could not be resolved to a commit at all — git
# missing, or a path outside any repo.  Stated rather than guessed: the value of
# the record is naming the code, so an invented SHA would be worse than none.
UNKNOWN_SHA = "unknown"

LOG_HEADER = """# Integration Runs

The record of this repo's integration suite — the one that launches real
players, a real dashboard and a real AHK bridge, and so can only run on the
user's Windows machine, off-screen, through:

    .venv/Scripts/python.exe -m tests.integration.hidden_desktop

There is no CI for it and there is not going to be one; the runner writes this
file itself, appending one row as it tears each run down.  So this is the whole
answer to "when did the integration suite last pass, and against what?" — and
the scope column is what keeps a green `-k nau` from reading as a green suite.

Oldest first, append-only, one row per run.  A SHA marked `-dirty` means
that checkout had uncommitted work when the run finished, so the commit
alone does not describe what ran.

| finished (UTC) | fun_time | player_core | result | counts | scope |
| --- | --- | --- | --- | --- | --- |
"""


@dataclass(frozen=True)
class RunCounts:
    """What pytest reported: the collected total and the ways it went wrong."""

    tests: int
    failures: int
    errors: int
    skipped: int

    def summary(self) -> str:
        """How the row says it: "52 passed, 2 failed, 1 error, 3 skipped".

        The passed count always, and each other outcome only when it happened,
        so a clean run reads as one number rather than a row of zeroes to scan
        past.  Skips are spelled out even though pytest still exits 0 on them,
        because this project's bar for green is that there are none.
        """
        parts = [f"{self.tests - self.failures - self.errors - self.skipped} passed"]
        for count, noun in ((self.failures, "failed"),
                            (self.errors, "error" if self.errors == 1 else "errors"),
                            (self.skipped, "skipped")):
            if count:
                parts.append(f"{count} {noun}")
        return ", ".join(parts)


def read_pytest_counts(report_path: Path) -> RunCounts | None:
    """The counts out of the JUnit report pytest wrote, or ``None`` if it wrote
    none — which is a run that died before it collected a test, and so has no
    result worth a row."""
    try:
        suite = ElementTree.parse(report_path).getroot().find("testsuite")
    except (OSError, ElementTree.ParseError):
        return None
    if suite is None:
        return None
    return RunCounts(
        tests=int(suite.get("tests", 0)),
        failures=int(suite.get("failures", 0)),
        errors=int(suite.get("errors", 0)),
        skipped=int(suite.get("skipped", 0)),
    )


def _git(path: Path, *args: str) -> str | None:
    """*args* run against the checkout *path* sits in, or ``None`` if git could
    not answer — it is missing, or the path is in no repo at all."""
    try:
        done = subprocess.run(["git", "-C", str(path), *args],
                              capture_output=True, text=True, check=False)
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def git_short_sha(path: Path) -> str:
    """HEAD of the checkout *path* sits in, abbreviated — or ``UNKNOWN_SHA``.

    Git walks up from *path* itself, so a package directory answers for its
    repo and a worktree answers with its own HEAD rather than the primary's.

    A tree that differs from that commit gets ``-dirty`` after the SHA, git's
    own convention.  Agents run this suite from a worktree mid-change and are
    told to commit the row with the branch, so without the mark a pass would be
    credited to a commit that was never what ran.  Untracked files count:
    a whole new module is exactly the kind of change that decides the result.
    """
    head = _git(path, "rev-parse", "--short", "HEAD")
    if head is None:
        return UNKNOWN_SHA
    changes = _git(path, "status", "--porcelain")
    return f"{head.strip()}-dirty" if changes and changes.strip() else head.strip()


def player_core_directory(project_dirs: Sequence[str]) -> Path | None:
    """The ``player_core`` package this run's code imports.

    Resolved the way the run resolves it: *project_dirs* — the checkout's
    ``genau_project_dirs``, which the root conftest puts on ``sys.path`` and the
    manifest puts on every child's PYTHONPATH — comes first, and the venv's
    editable install answers when none of them holds a ``player_core``.  The
    usual pin is a genau worktree with no ``player_core`` in it at all, which is
    why an entry only counts when the package is really there.
    """
    for entry in project_dirs:
        package = Path(entry) / "player_core"
        if (package / "__init__.py").is_file():
            return package
    spec = importlib.util.find_spec("player_core")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent


def format_run_line(
    *,
    timestamp: str,
    fun_time_sha: str,
    player_core_sha: str,
    exit_code: int,
    counts: RunCounts,
    extra_args: list[str],
) -> str:
    """One markdown table row recording a finished run.

    The scope is quoted the way the runner itself builds a command line, so an
    arg carrying a space stays one arg — the column is what a later reader
    re-runs to reproduce the row.
    """
    result = "PASS" if exit_code == 0 else "FAIL"
    scope = f"`{subprocess.list2cmdline(extra_args)}`" if extra_args else "full suite"
    return (
        f"| {timestamp} | {fun_time_sha} | {player_core_sha} | {result} "
        f"| {counts.summary()} | {scope} |"
    )


def append_run_line(log_path: Path, line: str) -> None:
    """Add *line* to the end of the log, seeding the header if it is not there.

    Append-only and never a rewrite: several agents run the suite from their own
    worktrees, so two runs landing at once have to conflict over their own two
    rows and nothing else.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.write_text(LOG_HEADER, encoding="utf-8")
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"{line}\n")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def record_run(
    *,
    repo_root: Path,
    report_path: Path,
    exit_code: int,
    extra_args: list[str],
    project_dirs: Sequence[str],
    now: Callable[[], datetime] = _utc_now,
) -> None:
    """Append this run's row to the repo's log.

    Writes nothing when pytest left no report: the run died before it collected
    a test, so there is no result to file against this code, and a row would
    still read as a run that happened.
    """
    counts = read_pytest_counts(report_path)
    if counts is None:
        return
    package = player_core_directory(project_dirs)
    append_run_line(repo_root / LOG_RELATIVE_PATH, format_run_line(
        timestamp=now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        fun_time_sha=git_short_sha(repo_root),
        player_core_sha=git_short_sha(package) if package else UNKNOWN_SHA,
        exit_code=exit_code,
        counts=counts,
        extra_args=extra_args,
    ))
