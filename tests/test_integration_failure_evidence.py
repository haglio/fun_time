"""A failed integration run keeps what its players logged.

Every test used to delete its session's temp root on the way out, so a failure
that only showed in a full run arrived with nothing to read, and the session
sent to fix it spent most of its budget making the failure happen again.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.integration import conftest, integration_support
from tests.integration.integration_support import clear_retired_roots, retire_temp_root


@pytest.fixture(autouse=True)
def _no_roots_left_between_tests():
    integration_support.RETIRED_ROOTS.clear()
    yield
    integration_support.RETIRED_ROOTS.clear()


def _a_session_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    state = root / "integration_runtime" / "state"
    state.mkdir(parents=True)
    (state / "main_player.log").write_text(f"{name} main player log", encoding="utf-8")
    (root / "integration_runtime" / "videos").mkdir()
    (root / "integration_runtime" / "videos" / "clip.mp4").write_bytes(b"not really a video")
    return root


def test_a_retired_root_stays_until_the_run_ends(tmp_path: Path):
    root = _a_session_root(tmp_path, "one")

    retire_temp_root(root)

    assert root.exists()


def test_a_passing_run_deletes_its_roots_and_keeps_nothing(tmp_path: Path):
    root = _a_session_root(tmp_path, "one")
    retire_temp_root(root)

    kept = clear_retired_roots(run_failed=False, keep_in=tmp_path / "kept")

    assert kept is None
    assert not root.exists()
    assert not (tmp_path / "kept").exists()


def test_a_failed_run_keeps_each_sessions_logs_before_its_roots_go(tmp_path: Path):
    roots = [_a_session_root(tmp_path, "one"), _a_session_root(tmp_path, "two")]
    for root in roots:
        retire_temp_root(root)

    kept = clear_retired_roots(run_failed=True, keep_in=tmp_path / "kept")

    assert all(not root.exists() for root in roots)
    logs = sorted(path.read_text(encoding="utf-8") for path in kept.rglob("main_player.log"))
    assert logs == ["one main player log", "two main player log"]


def test_the_media_a_session_linked_is_not_kept(tmp_path: Path):
    retire_temp_root(_a_session_root(tmp_path, "one"))

    kept = clear_retired_roots(run_failed=True, keep_in=tmp_path / "kept")

    assert not list(kept.rglob("clip.mp4"))


def test_two_failed_runs_keep_their_logs_apart(tmp_path: Path):
    retire_temp_root(_a_session_root(tmp_path, "first"))
    first = clear_retired_roots(run_failed=True, keep_in=tmp_path / "kept")
    retire_temp_root(_a_session_root(tmp_path, "second"))
    second = clear_retired_roots(run_failed=True, keep_in=tmp_path / "kept")

    assert first != second
    assert (first.parent == second.parent == tmp_path / "kept")


def test_the_run_hands_its_outcome_to_the_cleanup_when_it_ends():
    with patch.object(conftest, "clear_retired_roots", return_value=None) as clear:
        conftest.pytest_sessionfinish(session=None, exitstatus=1)
        conftest.pytest_sessionfinish(session=None, exitstatus=0)

    assert [call.kwargs["run_failed"] for call in clear.call_args_list] == [True, False]
    assert {call.kwargs["keep_in"] for call in clear.call_args_list} == {conftest.FAILURE_EVIDENCE}


def test_failure_evidence_lands_in_the_checkouts_ignored_state_dir():
    checkout = Path(__file__).resolve().parents[1]
    assert checkout / "state" / "integration_failures" == conftest.FAILURE_EVIDENCE
