"""Which sibling checkouts a session runs, said by the checkout itself."""
from __future__ import annotations

from pathlib import Path

from fun_time import checkout_overrides
from fun_time.checkout_overrides import (
    GENAU_DIRS_OVERRIDE_NAME,
    ORIGENERATOR_DIR_OVERRIDE_NAME,
    apply_genau_dirs_to_sys_path,
    apply_origenerator_dir_override,
    override_lines,
)


class TestOverrideLines:
    def test_an_absent_file_is_not_an_empty_one(self, tmp_path: Path):
        """None and [] are different answers: absent means the machine's config
        still decides, empty means this checkout has overruled it."""
        assert override_lines(tmp_path / "nothing.txt") is None
        (tmp_path / "empty.txt").write_text("", encoding="utf-8")
        assert override_lines(tmp_path / "empty.txt") == []

    def test_comments_and_blank_lines_are_not_entries(self, tmp_path: Path):
        path = tmp_path / "dirs.txt"
        path.write_text("# a note\n\n  C:/one  \nC:/two\n", encoding="utf-8")
        assert override_lines(path) == ["C:/one", "C:/two"]


def test_the_sys_path_override_only_adds_directories_that_exist(tmp_path, monkeypatch):
    """A path naming a checkout that has been retired would otherwise sit in
    front of the venv's install, shadowing nothing and finding nothing."""
    state = tmp_path / "state"
    state.mkdir()
    real = tmp_path / "real_checkout"
    real.mkdir()
    (state / GENAU_DIRS_OVERRIDE_NAME).write_text(
        f"{real}\nC:/gone/for/good\n", encoding="utf-8")
    monkeypatch.setattr(checkout_overrides.config_module, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(checkout_overrides, "sys", type("s", (), {"path": []})())

    assert apply_genau_dirs_to_sys_path() == [str(real)]


def test_the_runtime_override_reaches_a_branch_that_introduces_the_key(tmp_path, monkeypatch, cfg_path):
    """The branch-config generator runs the PRIMARY checkout's copy of this
    module, so a branch that INTRODUCES the override cannot rely on it — the
    orchestrator applies the file against its own checkout at launch instead."""
    from fun_time.config import load_config

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / ORIGENERATOR_DIR_OVERRIDE_NAME).write_text(
        "C:/origenerator/.claude/worktrees/mine\n", encoding="utf-8")
    monkeypatch.setattr(checkout_overrides.config_module, "PROJECT_DIR", tmp_path)

    config = apply_origenerator_dir_override(load_config(cfg_path))

    assert config.paths.origenerator_dir == Path("C:/origenerator/.claude/worktrees/mine")


def test_the_runtime_override_yields_to_an_integration_run(tmp_path, monkeypatch, cfg_path):
    """An integration run's config decides what it hosts — isolation strips the
    key, and the origenerator-mode test names its own stub.  The override
    out-ranking them made every session the suite launched from a worktree
    carrying the file host the REAL app: the machine's one ComfyUI, booted on
    the hidden desktop by a test run.

    Told by its argument, not by the environment: the process edge reads the
    switch once and every layer below takes the answer.
    """
    from fun_time.config import load_config

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / ORIGENERATOR_DIR_OVERRIDE_NAME).write_text(
        "C:/origenerator/.claude/worktrees/mine\n", encoding="utf-8")
    monkeypatch.setattr(checkout_overrides.config_module, "PROJECT_DIR", tmp_path)

    config = apply_origenerator_dir_override(
        load_config(cfg_path), integration=True)

    assert config.paths.origenerator_dir is None  # the config's own answer stands
