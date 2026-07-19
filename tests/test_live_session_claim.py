"""Unit tests: a running Fun Time says so somewhere every checkout can see.

A worktree's config resolves ``state_dir`` to that worktree, so nothing that
looks in its own state dir can see the session running out of the primary
checkout.  The claim is the one machine-global rendezvous point that makes a
live session findable no matter which checkout is asking.
"""
from __future__ import annotations

from pathlib import Path

from unittest.mock import patch

from fun_time import live_session
from fun_time.live_session import (
    CLAIM_PATH_ENV_VAR,
    default_claim_path,
    publish_live_session,
    live_session_state_dir,
)


class TestDefaultClaimPath:
    def test_an_override_relocates_the_claim(self, tmp_path, monkeypatch):
        """The unit suite points this somewhere disposable.  Without it every test
        that runs the orchestrator would publish a real claim naming the live
        pytest process — and a concurrent integration run would read that, believe
        the user had opened Fun Time, and abort."""
        monkeypatch.setenv(CLAIM_PATH_ENV_VAR, str(tmp_path / "elsewhere.ini"))

        assert default_claim_path() == tmp_path / "elsewhere.ini"


class TestPublishLiveSession:
    def test_claim_names_the_running_session_state_dir(self, tmp_path):
        claim_file = tmp_path / "live_session.ini"

        publish_live_session(tmp_path / "state", claim_file=claim_file)

        assert live_session_state_dir(claim_file=claim_file) == Path(tmp_path / "state")


class TestReadLiveSession:
    def test_no_claim_when_nothing_ever_published_one(self, tmp_path):
        assert live_session_state_dir(claim_file=tmp_path / "live_session.ini") is None

    def test_a_claim_whose_process_has_exited_is_no_claim(self, tmp_path):
        """The file outlives the session that wrote it, so liveness comes from
        the process, never from the file existing."""
        claim_file = tmp_path / "live_session.ini"
        publish_live_session(tmp_path / "state", claim_file=claim_file)

        with patch.object(live_session, "get_process_creation_time", return_value=None):
            assert live_session_state_dir(claim_file=claim_file) is None
