"""What is left when an integration run ends: what its players logged if it
failed, and nothing else.

Every test used to delete its session's temp root on the way out, so a failure
that only showed in a full run arrived with nothing to read, and the session
sent to fix it spent most of its budget making the failure happen again.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.integration import conftest, integration_support
from tests.integration.integration_support import (
    RootsLeftBehind,
    build_integration_temp_root,
    clear_run_roots,
    keep_every_sessions_logs,
)


@pytest.fixture(autouse=True)
def _no_roots_left_between_tests():
    integration_support.RUN_ROOTS.clear()
    yield
    integration_support.RUN_ROOTS.clear()


def _a_session_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    state = root / "integration_runtime" / "state"
    state.mkdir(parents=True)
    (state / "main_player.log").write_text(f"{name} main player log", encoding="utf-8")
    (root / "integration_runtime" / "videos").mkdir()
    (root / "integration_runtime" / "videos" / "clip.mp4").write_bytes(b"not really a video")
    integration_support.RUN_ROOTS.append(root)
    return root


def test_a_root_the_run_built_goes_without_the_test_that_built_it_asking(
    tmp_path: Path, monkeypatch,
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    root = build_integration_temp_root()

    clear_run_roots()

    assert not root.exists()


def test_a_root_that_will_not_go_is_named_and_the_others_still_do(tmp_path: Path):
    held = _a_session_root(tmp_path, "held")
    goes = _a_session_root(tmp_path, "goes")

    with (held / "integration_runtime" / "state" / "main_player.log").open("a"):
        with pytest.raises(RootsLeftBehind, match="held"):
            clear_run_roots(budget_s=0)

    assert not goes.exists()


def test_a_player_still_on_its_way_out_is_waited_for(tmp_path: Path):
    """The last session's players are killed with taskkill /F, which returns
    before they have finished going."""
    root = _a_session_root(tmp_path, "going")

    with (root / "integration_runtime" / "state" / "main_player.log").open("a") as log:
        clear_run_roots(budget_s=60.0, sleep=lambda _seconds: log.close())

    assert not root.exists()


def test_the_wait_outlasts_the_slowest_player_measured_on_a_busy_machine():
    """59 seconds from a session's stop to its last player gone, with the flake
    gate keeping every core busy."""
    assert integration_support.RELEASE_BUDGET_S >= 2 * 59


def test_a_failed_run_keeps_each_sessions_logs(tmp_path: Path):
    roots = [_a_session_root(tmp_path, "one"), _a_session_root(tmp_path, "two")]

    kept = keep_every_sessions_logs(tmp_path / "kept")

    assert all(root.exists() for root in roots)
    logs = sorted(path.read_text(encoding="utf-8") for path in kept.rglob("main_player.log"))
    assert logs == ["one main player log", "two main player log"]


def test_the_media_a_session_linked_is_not_kept(tmp_path: Path):
    _a_session_root(tmp_path, "one")

    kept = keep_every_sessions_logs(tmp_path / "kept")

    assert not list(kept.rglob("clip.mp4"))


def test_a_run_that_built_no_roots_keeps_nothing(tmp_path: Path):
    assert keep_every_sessions_logs(tmp_path / "kept") is None
    assert not (tmp_path / "kept").exists()


def test_two_failed_runs_keep_their_logs_apart(tmp_path: Path):
    _a_session_root(tmp_path, "first")
    first = keep_every_sessions_logs(tmp_path / "kept")
    integration_support.RUN_ROOTS.clear()
    _a_session_root(tmp_path, "second")
    second = keep_every_sessions_logs(tmp_path / "kept")

    assert first != second
    assert (first.parent == second.parent == tmp_path / "kept")


def test_only_a_failed_run_keeps_its_logs_and_either_way_its_roots_go():
    with (
        patch.object(conftest, "keep_every_sessions_logs", return_value=None) as keep,
        patch.object(conftest, "clear_run_roots") as clear,
    ):
        conftest.pytest_sessionfinish(session=None, exitstatus=1)
        conftest.pytest_sessionfinish(session=None, exitstatus=0)

    assert [call.args for call in keep.call_args_list] == [(conftest.FAILURE_EVIDENCE,)]
    assert clear.call_count == 2


def test_a_root_left_behind_ends_the_run_saying_which():
    refusal = RootsLeftBehind("C:/Temp/fun_time_integration_abc: still open")
    with patch.object(conftest, "clear_run_roots", side_effect=refusal):
        with pytest.raises(pytest.exit.Exception, match="fun_time_integration_abc") as ended:
            conftest.pytest_sessionfinish(session=None, exitstatus=0)

    assert ended.value.returncode == pytest.ExitCode.TESTS_FAILED


def test_failure_evidence_lands_in_the_checkouts_ignored_state_dir():
    checkout = Path(__file__).resolve().parents[1]
    assert checkout / "state" / "integration_failures" == conftest.FAILURE_EVIDENCE
