"""Which sibling checkouts a session runs, said by the checkout itself."""
from __future__ import annotations

from pathlib import Path

from fun_time import checkout_overrides, project_paths
from fun_time.checkout_overrides import (
    ORIGENERATOR_DIR_OVERRIDE_NAME,
    apply_this_checkouts_overrides,
)
from fun_time.config import load_config
from fun_time.project_paths import (
    SIBLING_CHECKOUTS_OVERRIDE_NAME,
    apply_sibling_checkouts_to_sys_path,
    override_lines,
    sibling_checkouts_named_in,
)


class TestTheFileThatNamesTheSiblingCheckouts:
    def test_is_called_after_them(self, tmp_path: Path):
        (tmp_path / "sibling_checkouts.txt").write_text("C:/funestra-fix\n", encoding="utf-8")
        assert sibling_checkouts_named_in(tmp_path) == ["C:/funestra-fix"]

    def test_a_worktree_from_before_the_rename_is_read_under_genaus_old_name(self, tmp_path: Path):
        """Every worktree on the machine wrote it under that name until
        2026-10-16, and each one rebased onto the rename still means it."""
        (tmp_path / "genau_project_dirs.txt").write_text("C:/funestra-fix\n", encoding="utf-8")
        assert sibling_checkouts_named_in(tmp_path) == ["C:/funestra-fix"]

    def test_the_new_name_wins_where_a_worktree_has_both(self, tmp_path: Path):
        (tmp_path / "sibling_checkouts.txt").write_text("", encoding="utf-8")
        (tmp_path / "genau_project_dirs.txt").write_text("C:/funestra-old\n", encoding="utf-8")
        assert sibling_checkouts_named_in(tmp_path) == []

    def test_a_worktree_that_names_none_leaves_it_to_the_machine(self, tmp_path: Path):
        assert sibling_checkouts_named_in(tmp_path) is None


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
    (state / SIBLING_CHECKOUTS_OVERRIDE_NAME).write_text(
        f"{real}\nC:/gone/for/good\n", encoding="utf-8")
    monkeypatch.setattr(project_paths, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(project_paths, "sys", type("s", (), {"path": []})())

    assert apply_sibling_checkouts_to_sys_path() == [str(real)]


def test_this_checkouts_own_packages_still_come_before_a_siblings(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    sibling = tmp_path / "sibling_checkout"
    sibling.mkdir()
    (state / SIBLING_CHECKOUTS_OVERRIDE_NAME).write_text(str(sibling), encoding="utf-8")
    monkeypatch.setattr(project_paths, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(project_paths, "sys", type("s", (), {"path": ["", "site-packages"]})())

    apply_sibling_checkouts_to_sys_path()

    assert project_paths.sys.path[:3] == [str(tmp_path), str(sibling), ""]


def test_a_checkout_with_no_siblings_named_leaves_the_path_as_it_found_it(tmp_path, monkeypatch):
    monkeypatch.setattr(project_paths, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(project_paths, "sys", type("s", (), {"path": ["", "site-packages"]})())

    apply_sibling_checkouts_to_sys_path()

    assert project_paths.sys.path == ["", "site-packages"]


def _this_checkout_names(tmp_path, monkeypatch, override: str, entry: str) -> None:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / override).write_text(f"{entry}\n", encoding="utf-8")
    monkeypatch.setattr(checkout_overrides, "PROJECT_DIR", tmp_path)


def test_the_runtime_override_reaches_a_branch_that_introduces_the_key(tmp_path, monkeypatch, cfg_path):
    """The branch-config generator runs the PRIMARY checkout's copy of this
    module, so a branch that INTRODUCES the override cannot rely on it — the
    orchestrator applies the file against its own checkout at launch instead."""
    _this_checkout_names(tmp_path, monkeypatch, ORIGENERATOR_DIR_OVERRIDE_NAME,
                         "C:/origenerator/.claude/worktrees/mine")

    config = apply_this_checkouts_overrides(load_config(cfg_path))

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
    _this_checkout_names(tmp_path, monkeypatch, ORIGENERATOR_DIR_OVERRIDE_NAME,
                         "C:/origenerator/.claude/worktrees/mine")

    config = apply_this_checkouts_overrides(load_config(cfg_path), integration=True)

    assert config.paths.origenerator_dir is None  # the config's own answer stands


def test_a_session_runs_the_sibling_checkouts_its_own_checkout_names(tmp_path, monkeypatch, cfg_path):
    """A production checkout from before 2026-10-16 reads only the file's old
    name when it writes a preview's config, so a branch that names its checkouts
    under the new one has them applied at launch, against its own checkout."""
    _this_checkout_names(tmp_path, monkeypatch, SIBLING_CHECKOUTS_OVERRIDE_NAME,
                         "C:/funestra/.claude/worktrees/mine")

    config = apply_this_checkouts_overrides(load_config(cfg_path))

    assert config.paths.sibling_checkouts == (Path("C:/funestra/.claude/worktrees/mine"),)


def test_an_integration_run_keeps_the_sibling_checkouts_its_config_names(
        tmp_path, monkeypatch, cfg_path):
    """A check that starts a Funestra on a checkout of its own -- one that dies
    at import, say -- names it in the run's config, which already carries this
    checkout's siblings."""
    _this_checkout_names(tmp_path, monkeypatch, SIBLING_CHECKOUTS_OVERRIDE_NAME,
                         "C:/funestra/.claude/worktrees/mine")

    config = apply_this_checkouts_overrides(load_config(cfg_path), integration=True)

    assert config.paths.sibling_checkouts == ()
