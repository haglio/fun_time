"""Tests for the pre-publication content guard.

Every "banned" term here is an invented placeholder — the guard's real
blocklist is git-ignored, and these tests must themselves stay publishable.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tools.sanitize_guard import (
    blocklist_path,
    find_violations,
    load_blocklist,
    scan_files,
)


class TestFindViolations:
    def test_flags_a_banned_single_word(self):
        found = find_violations("this has forbiddenterm in it", ["forbiddenterm"])
        assert [(v.term, v.line) for v in found] == [("forbiddenterm", 1)]

    def test_is_case_insensitive(self):
        assert find_violations("FORBIDDENTERM", ["forbiddenterm"])

    def test_matches_a_multi_word_term_across_flexible_whitespace(self):
        assert find_violations("a two   word phrase", ["two word"])

    def test_matches_a_term_a_line_wrap_has_split(self):
        """A per-line scan cannot see this. A real title hid behind a docstring's
        line break through every scan, and only surfaced when a history rewrite
        matched on the whole blob and put it back together.
        """
        found = find_violations("a title like *two\n    word* would match", ["two word"])
        assert [v.line for v in found] == [1]  # reported where the match starts

    def test_a_line_number_still_points_at_the_right_line(self):
        found = find_violations("clean\nclean\nhas badterm\nclean", ["badterm"])
        assert [v.line for v in found] == [3]

    def test_matches_a_multi_word_term_joined_the_way_a_filename_joins_it(self):
        """The list is written in prose; the leak arrives as a filename. Real
        names sat on a public `main` in exactly these shapes, unflagged, because
        the matcher allowed only whitespace between a term's words.
        """
        for slug in ("two-word", "two_word", "two.word", "twoword"):
            assert find_violations(f"clip-{slug}-scene-a.mp4", ["two word"]), slug

    def test_matches_an_inflected_form(self):
        """`badterm` on the list did not catch `badterms` in prose: the trailing
        word boundary refused the plural.
        """
        for form in ("badterms", "badterm's", "badtermed", "badterming"):
            assert find_violations(f"the {form} here", ["badterm"]), form

    def test_widening_still_refuses_an_unrelated_longer_word(self):
        """Separator and inflection slack must not decay into a substring match:
        `cat` may reach `cat-s`, never `concatenated`.
        """
        assert find_violations("a concatenated list", ["cat"]) == []
        assert find_violations("scatter the words", ["cat"]) == []
        assert find_violations("a category error", ["cat"]) == []

    def test_punctuated_term_matches_literally(self):
        assert find_violations("go to site.example now", ["site.example"])

    def test_each_term_on_a_line_is_reported(self):
        found = find_violations("alpha and beta together", ["alpha", "beta"])
        assert {v.term for v in found} == {"alpha", "beta"}

    def test_excerpt_redacts_every_matched_term(self):
        found = find_violations("keep alpha drop beta", ["alpha", "beta"])
        assert all("alpha" not in v.excerpt and "beta" not in v.excerpt for v in found)
        assert all("***" in v.excerpt for v in found)

    def test_clean_text_has_no_violations(self):
        assert find_violations("perfectly clean text", ["badterm", "two word"]) == []


class TestLoadBlocklist:
    def test_reads_terms_skipping_blanks_and_comments(self, tmp_path: Path):
        f = tmp_path / "bl.txt"
        f.write_text("# a comment\nalpha\n\n  beta gamma  \n", encoding="utf-8")
        assert load_blocklist(f) == ["alpha", "beta gamma"]


class TestScanFiles:
    def test_collects_violations_with_paths_relative_to_root(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("has badterm", encoding="utf-8")
        (tmp_path / "b.txt").write_text("clean", encoding="utf-8")
        found = scan_files([tmp_path / "a.txt", tmp_path / "b.txt"], ["badterm"], root=tmp_path)
        assert [(v.path, v.term) for v in found] == [("a.txt", "badterm")]

    def test_skips_undecodable_binary_files(self, tmp_path: Path):
        (tmp_path / "img.bin").write_bytes(b"\x00\xff\xfe badterm \x00")
        assert scan_files([tmp_path / "img.bin"], ["badterm"], root=tmp_path) == []


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


class TestBlocklistPath:
    """The blocklist is git-ignored, so only its resolution keeps the guard alive."""

    def test_uses_this_checkout_when_the_blocklist_is_here(self, tmp_path: Path):
        (tmp_path / "sanitize").mkdir()
        here = tmp_path / "sanitize" / "blocklist.local.txt"
        here.write_text("alpha\n", encoding="utf-8")
        assert blocklist_path(tmp_path) == here

    def test_falls_back_to_the_primary_checkout_from_a_worktree(self, tmp_path: Path):
        """The regression this whole helper exists for: a worktree never has the
        git-ignored overlay, so resolving it locally left the guard toothless
        wherever the work actually happens.
        """
        primary = tmp_path / "primary"
        primary.mkdir()
        _git(primary, "init", "-b", "main")
        _git(primary, "config", "user.email", "guard@example.test")
        _git(primary, "config", "user.name", "Guard Test")
        (primary / "sanitize").mkdir()
        real = primary / "sanitize" / "blocklist.local.txt"
        real.write_text("alpha\n", encoding="utf-8")
        (primary / "README.md").write_text("hi\n", encoding="utf-8")
        _git(primary, "add", "README.md")
        _git(primary, "commit", "-m", "seed")

        tree = tmp_path / "tree"
        _git(primary, "worktree", "add", str(tree), "-b", "side")
        assert not (tree / "sanitize" / "blocklist.local.txt").exists()
        assert blocklist_path(tree) == real.resolve()

    def test_returns_a_missing_path_when_no_checkout_has_one(self, tmp_path: Path):
        """The public-clone case: no blocklist here, none in the primary either.
        Absence must read as "nothing to enforce" — a returned path that simply
        does not exist — never a crash. Same outcome when git is missing entirely,
        which the helper swallows for the benefit of a source tree with no repo.
        """
        clone = tmp_path / "clone"
        clone.mkdir()
        _git(clone, "init", "-b", "main")
        assert not blocklist_path(clone).exists()


class TestHookEntryPoint:
    """The CLI the git hooks call. Each case builds a throwaway repo and drives
    the real hooks through ``git commit``, because what matters is not that
    ``main()`` returns 1 — it is that git refuses the commit.
    """

    # A nonce, because the fixture repo stages a copy of the guard's own source
    # and that source spells `badterm` in its docstrings — using `badterm` here
    # would make every case fail (or pass) on the guard file rather than on the
    # fixture under test.
    TERM = "nonceterm"

    def _repo(self, tmp_path: Path, terms: str | None) -> Path:
        repo = tmp_path / "repo"
        (repo / "sanitize").mkdir(parents=True)
        # Ignored here exactly as in the real repos. It matters to the fixture:
        # the blocklist necessarily contains every term, so a staged copy of it
        # trips the hook — which is the right answer for a real repo and the
        # wrong setup for a test.
        (repo / ".gitignore").write_text(
            "sanitize/blocklist.local.txt\n", encoding="utf-8")
        if terms is not None:
            (repo / "sanitize" / "blocklist.local.txt").write_text(
                terms, encoding="utf-8")
        here = Path(__file__).resolve().parent.parent
        for rel in ("tools/__init__.py", "tools/sanitize_guard.py",
                    "tools/githooks/install.py",
                    "tools/githooks/pre-commit", "tools/githooks/commit-msg"):
            dest = repo / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((here / rel).read_bytes())
        _git(repo, "init", "-b", "main")
        _git(repo, "config", "user.email", "guard@example.test")
        _git(repo, "config", "user.name", "Guard Test")
        # Armed the way a real clone is armed — through the installer — so a
        # changed hooks path fails here instead of leaving clones unguarded
        # while a hand-spelled "tools/githooks" kept these tests green.
        subprocess.run(
            [sys.executable, str(repo / "tools" / "githooks" / "install.py")],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        return repo

    def _commit(self, repo: Path, message: str = "seed"):
        return subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", message],
            capture_output=True, text=True,
        )

    def test_the_hook_refuses_a_staged_banned_term(self, tmp_path: Path):
        repo = self._repo(tmp_path, f"{self.TERM}\n")
        (repo / "notes.md").write_text(f"this has {self.TERM} in it\n", encoding="utf-8")
        _git(repo, "add", ".")
        done = self._commit(repo)
        assert done.returncode != 0
        assert "blocked term" in done.stderr
        assert self.TERM not in done.stderr  # redacted, never echoed back

    def test_the_hook_refuses_a_banned_term_in_the_message(self, tmp_path: Path):
        repo = self._repo(tmp_path, f"{self.TERM}\n")
        (repo / "notes.md").write_text("clean\n", encoding="utf-8")
        _git(repo, "add", ".")
        assert self._commit(repo, f"drop the {self.TERM} fixture").returncode != 0

    def test_it_judges_the_staged_half_not_the_working_copy(self, tmp_path: Path):
        """A file staged clean and then dirtied must still commit: the index is
        what becomes the commit, so reading from disk would block the wrong thing.
        """
        repo = self._repo(tmp_path, f"{self.TERM}\n")
        f = repo / "notes.md"
        f.write_text("clean\n", encoding="utf-8")
        _git(repo, "add", ".")
        f.write_text(f"now with {self.TERM}\n", encoding="utf-8")
        assert self._commit(repo).returncode == 0

    def test_a_clean_commit_passes(self, tmp_path: Path):
        repo = self._repo(tmp_path, f"{self.TERM}\n")
        (repo / "notes.md").write_text("perfectly clean\n", encoding="utf-8")
        _git(repo, "add", ".")
        assert self._commit(repo).returncode == 0

    def test_no_blocklist_means_no_enforcement(self, tmp_path: Path):
        """A public clone has no overlay. It must commit normally, not be told
        the guard cannot run.
        """
        repo = self._repo(tmp_path, None)
        (repo / "notes.md").write_text(f"this has {self.TERM} in it\n", encoding="utf-8")
        _git(repo, "add", ".")
        assert self._commit(repo).returncode == 0


def test_no_blocklisted_terms_in_the_tracked_tree():
    """Enforcement: with the real (git-ignored) blocklist present, no tracked
    file may contain a banned term — reintroducing one fails the suite. A public
    checkout has no blocklist, so there is nothing to enforce (deliberately not
    a skip, so the run stays clean either way) — but the walk itself must never
    pass vacuously: a fabricated positive control proves the scan reads this
    tree even on the runs where the real list is absent.
    """
    repo = Path(__file__).resolve().parent.parent
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert tracked, "the tracked-tree walk saw no files at all"

    # Positive control: a term this very file is guaranteed to contain.  If
    # the scanner ever stops seeing the tree, this fails on every run — CI
    # included — instead of the guard silently reporting a pass that scanned
    # nothing.
    control = "sanitizeguardcontrolterm"  # lives only on this line, found here
    probe = scan_files((repo / rel for rel in tracked), [control], root=repo)
    assert any(str(v.path).endswith("test_sanitize_guard.py") for v in probe), (
        "the tracked-tree scan did not see this file's own control term"
    )

    blocklist = blocklist_path(repo)
    terms = load_blocklist(blocklist) if blocklist.exists() else []
    if not terms:
        return
    violations = scan_files((repo / rel for rel in tracked), terms, root=repo)
    # Print only the redacted excerpt, never the matched term itself.
    assert not violations, "blocklisted terms in tracked files:\n" + "\n".join(
        f"  {v.path}:{v.line}  {v.excerpt}" for v in violations[:20]
    )


class TestTheFlagsTheHookPasses:
    """This runs inside a git hook, where a usage mistake used to be a
    traceback and a misspelling used to be silently the other mode."""

    def test_a_message_with_no_file_is_a_usage_error_not_a_traceback(self):
        """`args[args.index("--message") + 1]` raised IndexError here."""
        from tools.sanitize_guard import build_parser

        with pytest.raises(SystemExit):
            build_parser().parse_args(["--message"])

    def test_a_misspelled_flag_is_refused_rather_than_read_as_the_other_mode(self):
        from tools.sanitize_guard import build_parser

        with pytest.raises(SystemExit):
            build_parser().parse_args(["--staged-changes"])

    def test_the_two_modes_are_exclusive(self):
        """One scan per run: a hook is either pre-commit or commit-msg."""
        from tools.sanitize_guard import build_parser

        with pytest.raises(SystemExit):
            build_parser().parse_args(["--staged", "--message", "MSG"])

    def test_each_mode_parses_on_its_own(self):
        from tools.sanitize_guard import build_parser

        assert build_parser().parse_args(["--staged"]).staged is True
        assert build_parser().parse_args(["--message", "MSG"]).message == "MSG"
