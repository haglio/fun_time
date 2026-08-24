"""Unit tests for the integration suite's committed run record.

The suite runs on the user's Windows machine, entered only through the
hidden-desktop runner, so nothing about a run is visible to anyone reading the
repo afterwards.  These pin the line that runner appends: what it says, and
above all that a narrowed run says so, since a green ``-k nau`` must never read
as a green suite.
"""
from __future__ import annotations

import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from tests.integration.run_log import (
    LOG_HEADER,
    LOG_RELATIVE_PATH,
    RunCounts,
    append_run_line,
    format_run_line,
    git_short_sha,
    player_core_directory,
    read_pytest_counts,
    record_run,
)


def test_a_full_green_run_reads_as_a_pass_row():
    line = format_run_line(
        timestamp="2026-08-24T18:03:11Z",
        fun_time_sha="b9fe768",
        player_core_sha="4a1c9de",
        exit_code=0,
        counts=RunCounts(tests=58, failures=0, errors=0, skipped=0),
        extra_args=[],
    )

    assert line == "| 2026-08-24T18:03:11Z | b9fe768 | 4a1c9de | PASS | 58 passed | full suite |"


def test_a_narrowed_run_names_the_args_that_narrowed_it():
    """The whole point of the record is that a partial run cannot pass for a
    full one, so the args that cut the suite down ride in the row itself."""
    line = format_run_line(
        timestamp="2026-08-24T18:03:11Z",
        fun_time_sha="b9fe768",
        player_core_sha="4a1c9de",
        exit_code=0,
        counts=RunCounts(tests=3, failures=0, errors=0, skipped=0),
        extra_args=["-k", "nau"],
    )

    assert line.endswith("| PASS | 3 passed | `-k nau` |")


def test_a_red_run_spells_out_every_way_it_went_wrong():
    line = format_run_line(
        timestamp="2026-08-24T18:03:11Z",
        fun_time_sha="b9fe768",
        player_core_sha="4a1c9de",
        exit_code=1,
        counts=RunCounts(tests=58, failures=2, errors=1, skipped=3),
        extra_args=[],
    )

    assert line.endswith("| FAIL | 52 passed, 2 failed, 1 error, 3 skipped | full suite |")


def test_a_skip_shows_even_though_pytest_calls_the_run_green():
    """This project's bar is zero skips, so a run pytest exits 0 on can still be
    one nobody should read as green — the counts have to say so."""
    line = format_run_line(
        timestamp="2026-08-24T18:03:11Z",
        fun_time_sha="b9fe768",
        player_core_sha="4a1c9de",
        exit_code=0,
        counts=RunCounts(tests=58, failures=0, errors=0, skipped=1),
        extra_args=[],
    )

    assert line.endswith("| PASS | 57 passed, 1 skipped | full suite |")


def test_more_than_one_error_is_plural_the_way_pytest_says_it():
    line = format_run_line(
        timestamp="2026-08-24T18:03:11Z",
        fun_time_sha="b9fe768",
        player_core_sha="4a1c9de",
        exit_code=1,
        counts=RunCounts(tests=58, failures=0, errors=2, skipped=0),
        extra_args=[],
    )

    assert "56 passed, 2 errors " in line


def test_the_first_run_seeds_the_file_with_its_header(tmp_path):
    log = tmp_path / "integration-runs.md"

    append_run_line(log, "| a row |")

    text = log.read_text(encoding="utf-8")
    assert text.startswith("# Integration Runs")
    assert text.endswith("| a row |\n")


def test_later_runs_append_under_the_rows_already_there(tmp_path):
    """Oldest first, one line per run, nothing rewritten — so two agents landing
    a run apiece conflict over one line rather than over the file."""
    log = tmp_path / "integration-runs.md"

    append_run_line(log, "| first |")
    append_run_line(log, "| second |")

    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[-2:] == ["| first |", "| second |"]
    assert lines.count("| first |") == 1


def test_the_counts_come_out_of_the_report_pytest_wrote(tmp_path):
    report = tmp_path / "report.xml"
    report.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites name="pytest tests">'
        '<testsuite name="pytest" errors="1" failures="2" skipped="3" tests="58" time="9.0">'
        '</testsuite></testsuites>',
        encoding="utf-8",
    )

    assert read_pytest_counts(report) == RunCounts(tests=58, failures=2, errors=1, skipped=3)


def test_a_run_that_wrote_no_report_has_no_counts_to_read(tmp_path):
    """pytest can die before it collects anything — the desktop guard refusing
    the invocation, an import blowing up in a conftest.  There is no result to
    record then, and inventing a zero-count row would put a green-looking line
    in the log for a run that never happened."""
    assert read_pytest_counts(tmp_path / "never-written.xml") is None


def _commit_a_throwaway_repo(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    (root / "README.md").write_text("example checkout\n", encoding="utf-8")
    for command in (["git", "init", "-q"],
                    ["git", "add", "README.md"],
                    ["git", "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
                     "commit", "-qm", "first"]):
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root,
                          check=True, capture_output=True, text=True).stdout.strip()


def test_the_sha_is_the_head_of_whatever_checkout_the_path_sits_in(tmp_path):
    checkout = tmp_path / "example_repo"
    expected = _commit_a_throwaway_repo(checkout)
    (checkout / "package").mkdir()

    assert git_short_sha(checkout / "package") == expected


def test_a_path_in_no_checkout_at_all_says_so_rather_than_guessing():
    """A row that quietly invented a SHA would be worse than one that admits it
    could not find one — the whole value of the record is naming the code.

    The system temp dir rather than ``tmp_path``: this suite's ``tmp_path``
    lives in ``.tmp-pytest-local`` inside the repo, so git finds fun_time's own
    HEAD from it.
    """
    with tempfile.TemporaryDirectory() as outside_any_repo:
        assert git_short_sha(Path(outside_any_repo)) == "unknown"


def test_with_no_override_the_player_core_is_the_venvs_install():
    package = player_core_directory([])

    assert package is not None
    assert package.name == "player_core"
    assert (package / "__init__.py").is_file()


def test_a_worktree_pinning_a_player_core_checkout_gets_that_one(tmp_path):
    """``genau_project_dirs`` goes in front of the venv for the run's children
    and for pytest's own ``sys.path``, so a branch leaning on an unlanded
    player_core change runs that checkout — and the row has to name it, or it
    credits the run to code it never executed."""
    branch = tmp_path / "player_core_branch"
    (branch / "player_core").mkdir(parents=True)
    (branch / "player_core" / "__init__.py").write_text("", encoding="utf-8")

    assert player_core_directory([str(branch)]) == branch / "player_core"


def test_an_override_naming_only_genau_leaves_player_core_where_it_was(tmp_path):
    """The usual pin is a genau worktree, which holds no ``player_core`` at all.
    Reporting that directory's HEAD would file the run under the wrong repo."""
    genau = tmp_path / "genau_branch"
    (genau / "genau").mkdir(parents=True)

    assert player_core_directory([str(genau)]) == player_core_directory([])


def _fixed_clock():
    return datetime(2026, 8, 24, 18, 3, 11, tzinfo=timezone.utc)


def _written_report(directory: Path, *, tests: int) -> Path:
    report = directory / "report.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest" errors="0" failures="0" '
        f'skipped="0" tests="{tests}"></testsuite></testsuites>',
        encoding="utf-8",
    )
    return report


def test_recording_a_run_appends_a_stamped_row_to_the_repos_log(tmp_path):
    checkout = tmp_path / "fun_time"
    sha = _commit_a_throwaway_repo(checkout)

    record_run(
        repo_root=checkout,
        report_path=_written_report(tmp_path, tests=58),
        exit_code=0,
        extra_args=[],
        project_dirs=[],
        now=_fixed_clock,
    )

    row = (checkout / LOG_RELATIVE_PATH).read_text(encoding="utf-8").splitlines()[-1]
    assert row.startswith(f"| 2026-08-24T18:03:11Z | {sha} | ")
    assert row.endswith("| PASS | 58 passed | full suite |")


def test_a_run_with_no_report_leaves_the_log_untouched(tmp_path):
    """No collected tests means nothing was proved about this code, and a row
    saying so would still read as a run that happened."""
    checkout = tmp_path / "fun_time"
    _commit_a_throwaway_repo(checkout)

    record_run(
        repo_root=checkout,
        report_path=tmp_path / "never-written.xml",
        exit_code=4,
        extra_args=[],
        project_dirs=[],
        now=_fixed_clock,
    )

    assert not (checkout / LOG_RELATIVE_PATH).exists()


def test_the_committed_log_is_the_header_the_runner_would_seed():
    """One definition of the table, so a column renamed in the document but not
    in ``format_run_line`` cannot quietly start writing crooked rows."""
    committed = Path(__file__).resolve().parents[1] / LOG_RELATIVE_PATH

    assert committed.read_text(encoding="utf-8").startswith(LOG_HEADER)


def test_every_row_in_the_committed_log_has_the_columns_the_header_declares():
    committed = (Path(__file__).resolve().parents[1] / LOG_RELATIVE_PATH
                 ).read_text(encoding="utf-8")
    columns = LOG_HEADER.strip().splitlines()[-2].count("|")

    for row in committed[len(LOG_HEADER):].splitlines():
        assert row.count("|") == columns, row


def test_a_checkout_with_uncommitted_work_is_marked_dirty(tmp_path):
    """Agents run this suite from a worktree mid-change, and the row is meant to
    be committed with that branch — so a bare SHA would credit the pass to code
    that was never what ran."""
    checkout = tmp_path / "example_repo"
    sha = _commit_a_throwaway_repo(checkout)
    (checkout / "README.md").write_text("edited since the commit\n", encoding="utf-8")

    assert git_short_sha(checkout) == f"{sha}-dirty"


def test_an_untracked_file_counts_as_dirty_too(tmp_path):
    """A whole new module is exactly the kind of change that decides whether the
    suite passes, and it is untracked until someone adds it."""
    checkout = tmp_path / "example_repo"
    sha = _commit_a_throwaway_repo(checkout)
    (checkout / "brand_new.py").write_text("VALUE = 1\n", encoding="utf-8")

    assert git_short_sha(checkout) == f"{sha}-dirty"


def test_an_arg_with_a_space_stays_one_arg_in_the_scope_column():
    """`-k "nau or genau"` is an ordinary way to narrow this suite, and the
    column is what a later reader re-runs to reproduce the row — joined bare it
    would read as four separate args."""
    line = format_run_line(
        timestamp="2026-08-24T18:03:11Z",
        fun_time_sha="b9fe768",
        player_core_sha="4a1c9de",
        exit_code=0,
        counts=RunCounts(tests=6, failures=0, errors=0, skipped=0),
        extra_args=["-k", "nau or genau"],
    )

    assert line.endswith('| `-k "nau or genau"` |')


def test_the_log_is_written_with_the_line_endings_gitattributes_pins_it_to(tmp_path):
    """``.gitattributes`` says ``eol=lf`` for this file, but the runner only ever
    writes it on Windows, where text mode would turn every row into CRLF and
    leave the checkout permanently disagreeing with the index.  This bites on
    the Windows merge gate, not on a POSIX box, where it passes either way."""
    log = tmp_path / "integration-runs.md"

    append_run_line(log, "| a row |")

    assert b"\r" not in log.read_bytes()
