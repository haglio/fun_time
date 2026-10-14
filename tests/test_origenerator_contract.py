from __future__ import annotations

import json
from pathlib import Path

import pytest

from fun_time.hosted_contract import CONTRACT_FILE
from tests import origenerator_contract
from tests.git_repo import git


def _publish(repo: Path, document: dict) -> None:
    (repo / CONTRACT_FILE).write_text(json.dumps(document), encoding="utf-8")
    git(repo, "add", CONTRACT_FILE)
    git(repo, "commit", "-q", "-m", "Publish the contract")


def _an_origin_and_a_checkout_of_it(tmp_path: Path) -> tuple[Path, Path]:
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    _publish(origin, {"shipped": "first"})
    checkout = tmp_path / "origenerator"
    git(tmp_path, "clone", "-q", "--no-hardlinks", str(origin), str(checkout))
    return origin, checkout


def _held_to(monkeypatch, *, named: Path | None, beside: Path | None) -> dict:
    monkeypatch.setattr(origenerator_contract, "named_checkout", lambda: named)
    monkeypatch.setattr(origenerator_contract, "_the_checkout_beside", lambda: beside)
    return origenerator_contract.published()


def test_the_checkout_beside_is_held_to_what_its_origin_shipped_not_to_its_working_tree(
        tmp_path, monkeypatch):
    origin, beside = _an_origin_and_a_checkout_of_it(tmp_path)
    _publish(origin, {"shipped": "since"})
    git(beside, "fetch", "-q")

    assert _held_to(monkeypatch, named=None, beside=beside) == {"shipped": "since"}


def test_a_branch_paired_with_an_origenerator_worktree_is_held_to_that_worktree_as_it_stands(
        tmp_path, monkeypatch):
    _origin, beside = _an_origin_and_a_checkout_of_it(tmp_path)
    paired = tmp_path / "paired"
    paired.mkdir()
    (paired / CONTRACT_FILE).write_text(json.dumps({"unshipped": True}), encoding="utf-8")

    assert _held_to(monkeypatch, named=paired, beside=beside) == {"unshipped": True}


def test_a_paired_worktree_that_publishes_nothing_leaves_the_shipped_one(tmp_path, monkeypatch):
    _origin, beside = _an_origin_and_a_checkout_of_it(tmp_path)
    paired = tmp_path / "paired"
    paired.mkdir()

    assert _held_to(monkeypatch, named=paired, beside=beside) == {"shipped": "first"}


def test_with_no_origenerator_checkout_beside_there_is_nothing_to_hold_to(monkeypatch):
    with pytest.raises(pytest.skip.Exception):
        _held_to(monkeypatch, named=None, beside=None)
