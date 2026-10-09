from __future__ import annotations

from tests.git_repo import git
from tests.integration import coverage_map


def test_a_changed_integration_test_file_is_run_by_itself():
    picked = coverage_map.tests_to_run(["tests/integration/test_crown_integration.py"], None)

    assert picked.files == {"tests/integration/test_crown_integration.py"}
    assert not picked.everything


def test_unit_tests_and_writing_that_never_ships_pick_no_integration_test():
    picked = coverage_map.tests_to_run(["tests/test_dashboard.py", "docs/resuming-a-session.md", "CLAUDE.md",
                                        ".github/workflows/merge-gate.yml", "tools/githooks/commit-msg"], None)

    assert picked == coverage_map.Picked()


def test_a_change_to_what_every_integration_test_shares_runs_them_all():
    for shared in ("tests/integration/integration_support.py", "tests/integration/conftest.py",
                   "windows_bridge_hotkeys.ahk", "launch.vbs", "pyproject.toml"):
        assert coverage_map.tests_to_run([shared], None).everything, shared


def a_map():
    return coverage_map.CoverageMap(
        commit="c3",
        ran={"tests/integration/test_crown_integration.py": frozenset({"fun_time/crown.py", "fun_time/orchestrator.py"}),
             "tests/integration/test_quit_integration.py": frozenset({"fun_time/orchestrator.py"})})


def test_a_changed_source_file_runs_the_integration_tests_that_ran_it():
    picked = coverage_map.tests_to_run(["fun_time/orchestrator.py"], a_map())

    assert picked.files == {"tests/integration/test_crown_integration.py", "tests/integration/test_quit_integration.py"}
    assert not picked.everything


def test_a_source_file_no_integration_test_was_seen_running_runs_them_all():
    assert coverage_map.tests_to_run(["fun_time/voice.py"], a_map()).everything


def test_without_a_map_a_changed_source_file_runs_them_all():
    assert coverage_map.tests_to_run(["fun_time/crown.py"], None).everything


def test_the_map_survives_being_saved_and_read_back(tmp_path):
    path = tmp_path / "integration_coverage_map.json"

    a_map().save(path)

    assert coverage_map.CoverageMap.load(path) == a_map()


def test_no_map_is_read_where_none_was_saved(tmp_path):
    assert coverage_map.CoverageMap.load(tmp_path / "integration_coverage_map.json") is None


def test_a_branch_s_change_is_what_it_committed_edited_and_added_since_it_left_main(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    for name in ("committed.py", "edited.py", "untouched.py"):
        (repo / name).write_text("first", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "first")
    git(repo, "checkout", "-b", "feature")
    (repo / "committed.py").write_text("second", encoding="utf-8")
    git(repo, "commit", "-am", "second")
    (repo / "edited.py").write_text("second", encoding="utf-8")
    (repo / "added.py").write_text("new", encoding="utf-8")

    assert coverage_map.changed_paths(repo, "main") == ["added.py", "committed.py", "edited.py"]


def test_what_is_picked_says_why_in_a_line():
    assert coverage_map.tests_to_run(["windows_bridge_hotkeys.ahk"], a_map()).reason() == (
        "running the whole integration suite, because the coverage map cannot say which tests run "
        "windows_bridge_hotkeys.ahk")
    assert coverage_map.tests_to_run(["tests/test_dashboard.py"], a_map()).reason() == (
        "no integration test runs what this change touched")
    assert coverage_map.tests_to_run(["fun_time/crown.py"], a_map()).reason() == (
        "running the integration tests that run what this change touched: "
        "tests/integration/test_crown_integration.py")
