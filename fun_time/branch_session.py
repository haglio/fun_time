"""Run a real session out of a branch worktree, so work can be judged before it lands.

Fun Time runs from the primary checkout, and that checkout only moves when
``main`` does — so a branch waiting on a pull request is code the user cannot
see, run or judge.  This is the third option between parking the branch and
landing it unverified: point a real session at the unlanded worktree, on the
real library, on the real monitors, first.

**A branch session replaces the live one; it never runs beside it.**  Nearly
everything a session touches is one-per-machine with no per-directory version:
the AHK hotkey shell is ``#SingleInstance Force`` and would evict the live
one's, the three UDP endpoints and the loopback port are fixed, there is one
microphone, one broker holding the OSR2's serial port, and one set of monitors
to be fullscreen on.  The integration suite escapes all of that by running on a
hidden desktop with those endpoints stripped
(``tests.integration.integration_support.isolate_shared_resources``); a
verification session cannot, because being watched on the real screen is the
entire point of it.

So rather than isolate them, two sessions are made impossible: the generated
config carries the live session's ``instance_id``, so both take the *same*
single-instance mutex and whichever starts second is refused with Fun Time's own
"already running" message.  That holds in either order — including the user
double-clicking the taskbar icon while a branch session is up.

What a branch session does get of its own is ``state/``: its command files,
playlists, logs, thumbnails and resume point live inside the worktree, so a
half-finished branch cannot corrupt what the live session reads back.
Everything else is deliberately the real thing — the real library, the real
``favs.csv``, the real broker and device — because a verification run on
fixtures verifies fixtures.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# PROJECT_DIR names whichever checkout imported the package, and this module is
# the one place that has to tell two checkouts apart — so it is read through the
# module at call time rather than bound once at import.
from . import config as config_module
from .config import DEFAULT_CONFIG_PATH, ProjectConfig, load_config

# Written into the worktree's own state dir, which is git-ignored — this file
# holds the machine's real library paths and must never be committable.
BRANCH_CONFIG_NAME = "fun_time_branch_config.json"

# The picker's menu, written where launch_branch.vbs reads it back.
WORKTREE_LIST_NAME = "branch_worktrees.txt"

STATE_DIRNAME = "state"
FIELD_SEPARATOR = "\t"

# How wide a menu line may be.  VBScript's InputBox truncates a prompt past
# roughly a thousand characters, and this repo carries dozens of worktrees, so
# the whole menu's budget is a few hundred.
LABEL_WIDTH = 64

# Git-ignored overlays that a session reads from its own checkout, so they exist
# in the primary and in no worktree.  See :func:`mirror_private_overlays`.
_PRIVATE_OVERLAYS = (
    Path("content.local.json"),
    Path("fun_time") / "static" / "regen_autofill.user.js",
)

# Superseded by the plural forms in :func:`_primary_resolved_values`.  A stale
# singular left in the file still names a path, and it is one this rewrite never
# pinned — so it goes rather than rides along into the worktree.
_SUPERSEDED_PATH_KEYS = ("portrait_dir", "landscape_dir")


def _git(args: list[str], cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def primary_checkout(start: Path | None = None) -> Path:
    """The checkout the live session runs from, found from any of them.

    Every worktree shares the primary's git directory, so that directory's
    parent names the primary wherever this is called from — the same lookup
    ``integration_support.real_config_path`` uses to find the machine's real
    config from a worktree.
    """
    start = start or config_module.PROJECT_DIR
    common = _git(["rev-parse", "--git-common-dir"], start).strip()
    return (start / common).resolve().parent


@dataclass(frozen=True)
class Worktree:
    """A checkout of this repo other than the primary, and what it is holding."""

    path: Path
    branch: str
    age: str
    subject: str

    @property
    def label(self) -> str:
        """One line naming the branch and its latest work, for the picker.

        Kept inside :data:`LABEL_WIDTH`, and the branch name is never what gives
        — it is the thing an agent hands the user, so the subject is cut
        instead.  The age is relative ("2 hours ago") because that is what
        identifies a branch to somebody who was awake for it; a date would not.
        """
        head, tail = f"{self.branch} — ", f" ({self.age})"
        room = LABEL_WIDTH - len(head) - len(tail)
        if room < 4:
            return f"{self.branch}{tail}"
        subject = self.subject if len(self.subject) <= room else f"{self.subject[:room - 1]}…"
        return f"{head}{subject}{tail}"


def _parse_worktree_records(porcelain: str) -> list[tuple[Path, str]]:
    """(path, branch) for each record of ``git worktree list --porcelain``.

    A record is a ``worktree`` line and the lines under it; a checkout that is
    not on a branch has ``detached`` there instead of ``branch``.
    """
    records: list[tuple[Path, str]] = []
    path: Path | None = None
    branch = "(detached)"
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            if path is not None:
                records.append((path, branch))
            path, branch = Path(line.removeprefix("worktree ")), "(detached)"
        elif line.startswith("branch "):
            branch = line.removeprefix("branch ").removeprefix("refs/heads/")
    if path is not None:
        records.append((path, branch))
    return records


def _last_commit(worktree: Path) -> tuple[int, str, str]:
    """(commit timestamp, how long ago in words, subject) of the tip commit."""
    line = _git(["log", "-1", "--format=%ct%x09%cr%x09%s"], worktree).strip()
    stamp, age, subject = line.split(FIELD_SEPARATOR, 2)
    return int(stamp), age, subject.replace(FIELD_SEPARATOR, " ")


def list_worktrees(primary: Path | None = None) -> list[Worktree]:
    """Every other checkout of this repo, most recent commit first.

    Git is the source of truth rather than a glob of ``.claude/worktrees``, so a
    worktree made by hand somewhere else is offered too.  A registered worktree
    whose directory is gone (deleted but not pruned) is skipped: it is a menu
    entry that could only fail.
    """
    primary = (primary or primary_checkout()).resolve()
    dated: list[tuple[int, Worktree]] = []
    for path, branch in _parse_worktree_records(_git(["worktree", "list", "--porcelain"], primary)):
        if not path.is_dir() or path.resolve() == primary:
            continue
        stamp, age, subject = _last_commit(path)
        dated.append((stamp, Worktree(path=path.resolve(), branch=branch, age=age, subject=subject)))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    return [worktree for _stamp, worktree in dated]


def _primary_resolved_values(real: ProjectConfig) -> dict[str, dict[str, object]]:
    """Every raw config value the loader resolves against its own checkout,
    paired with what it resolved to in the primary.

    ``config._resolve_path`` anchors a relative value on ``config.PROJECT_DIR``,
    which is whichever checkout imported the package — so the same config file
    read from a worktree names files *inside that worktree*.  The live config
    leans on that for several: ``favs.csv`` (the user's favorites, which would
    come up empty), ``../broker/launch_broker_tray.vbs`` (one directory up from
    the primary, which from a worktree is nothing at all), and the Chrome
    shortcut, which is untracked and so exists in no worktree.  Pinning each to
    what the primary resolved keeps a branch session on the machine's real
    files.

    Every value comes from *real*, the config as production loaded it — nothing
    here re-implements path resolution.  ``state_dir`` is listed for the same
    reason: this is the complete set, and the state dir being overridden
    afterwards is an exception on purpose rather than an omission.
    """
    paths, browser, regen = real.paths, real.random_favs_browser, real.regen
    return {
        "paths": {
            "ahk_exe": paths.ahk_exe,
            "python_exe": paths.python_exe,
            "nau_library_dirs": paths.nau_library_dirs,
            "portrait_dirs": paths.portrait_dirs,
            "landscape_dirs": paths.landscape_dirs,
            "weird_dir": paths.weird_dir,
            "clips_dir": paths.clips_dir,
            "audio_dir": paths.audio_dir,
            "favs_file": paths.favs_file,
            "state_dir": paths.state_dir,
            "genau_python_exe": paths.genau_python_exe,
            "genau_config_path": paths.genau_config_path,
            "broker_tray_launcher": paths.broker_tray_launcher,
        },
        "random_favs_browser": {
            "shortcut_path": browser.shortcut_path,
            "user_data_dir": browser.user_data_dir,
        },
        "regen": {
            "media_root": regen.media_root,
            "metadata_root": regen.metadata_root,
        },
        "vr": {
            "library_dirs": real.vr.library_dirs,
        },
    }


def _pin_paths_to_the_primary(raw: dict, real: ProjectConfig) -> None:
    """Rewrite *raw* in place so no value depends on which checkout reads it."""
    for section, values in _primary_resolved_values(real).items():
        for key, value in values.items():
            if value is None:
                # An absent optional stays absent; writing "None" would make
                # validate_config demand a file by that name.
                raw.get(section, {}).pop(key, None)
            elif isinstance(value, tuple):
                raw.setdefault(section, {})[key] = [str(item) for item in value]
            else:
                raw.setdefault(section, {})[key] = str(value)
    for key in _SUPERSEDED_PATH_KEYS:
        raw.get("paths", {}).pop(key, None)


def mirror_private_overlays(primary: Path, worktree: Path) -> list[Path]:
    """Copy into *worktree* the git-ignored overlays a session reads from its
    own checkout, and return what was copied.

    ``content.local.json`` (the real filter vocabulary) and the Provider
    autofill userscript are private overlays: git-ignored, so they sit in the
    primary checkout and in no worktree, and each is found relative to the
    package that reads it rather than through config.  A branch session without
    them falls back to the committed placeholders — the library browser's
    filters come up as ``alpha``/``beta``/``gamma`` — and the user is looking at
    a wrongness their branch did not cause.  Refreshed on every launch, so an
    edit to the real one is never a stale copy away.

    ``fun_time_config.json`` is deliberately not among them: the branch config
    written under ``state/`` is the whole point, and a copy of the real one
    loose in a worktree is a live config that nobody passes ``--config``.
    """
    copied: list[Path] = []
    for relative in _PRIVATE_OVERLAYS:
        source = primary / relative
        if not source.is_file():
            continue
        destination = worktree / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        copied.append(destination)
    return copied


def build_branch_config(
    worktree: Path,
    *,
    primary_config_path: Path = DEFAULT_CONFIG_PATH,
    primary: Path | None = None,
) -> Path:
    """Write the config a session in *worktree* runs on, and return its path.

    It is the live config with three changes: every path pinned to what the
    primary resolved, ``state_dir`` moved into the worktree, and the live
    session's ``instance_id`` carried over so the two can never both be up.
    """
    worktree = worktree.resolve()
    primary = (primary or config_module.PROJECT_DIR).resolve()
    if worktree == primary:
        raise ValueError(
            f"{worktree} is the checkout Fun Time already runs from — launch that one "
            "with launch.vbs.  A branch session needs a worktree of its own, because "
            "the state directory is the one thing it must not share."
        )
    if not (worktree / "fun_time" / "orchestrator.py").is_file():
        raise FileNotFoundError(f"{worktree} is not a Fun Time checkout")

    # Read the way the live session reads it — anchored on the primary — rather
    # than on whichever checkout this launcher happens to have been started from.
    real = load_config(primary_config_path, project_dir=primary)
    raw = json.loads(primary_config_path.read_text(encoding="utf-8"))
    _pin_paths_to_the_primary(raw, real)

    state_dir = worktree / STATE_DIRNAME
    raw["paths"]["state_dir"] = str(state_dir)
    raw["instance_id"] = real.instance_id

    mirror_private_overlays(primary, worktree)

    state_dir.mkdir(parents=True, exist_ok=True)
    destination = state_dir / BRANCH_CONFIG_NAME
    destination.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return destination


def launch(worktree: Path, **kwargs) -> int:
    """Build the branch config and run a session on it out of *worktree*.

    The orchestrator starts on *this* interpreter — the primary checkout's venv,
    the only python that has fun_time's sibling packages — but with its working
    directory in the worktree, and that is what swaps the code: ``fun_time`` is
    not installed into the venv at all, so ``python -m fun_time.orchestrator``
    resolves the package from the working directory.  Every child the session
    launches (the satellites, the dashboard, the audio companion, the loading
    and closing screens) inherits that directory and runs the branch's code too.

    Returns the session's exit code once it has quit, so the launcher's console
    log and its exited sentinel both describe the whole run.
    """
    worktree = worktree.resolve()
    config_path = build_branch_config(worktree, **kwargs)
    command = [sys.executable, "-m", "fun_time.orchestrator", "--config", str(config_path)]
    print(f"Running {subprocess.list2cmdline(command)}\n  in {worktree}", flush=True)
    return subprocess.run(command, cwd=str(worktree), check=False).returncode


def write_worktree_list(destination: Path, worktrees: list[Worktree]) -> None:
    """Write the picker's menu where ``launch_branch.vbs`` reads it back.

    UTF-16 with a BOM: it is the one encoding VBScript's FileSystemObject reads
    losslessly, and a commit subject with an em dash in it comes back mangled
    from anything else.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(f"{worktree.path}{FIELD_SEPARATOR}{worktree.label}\n" for worktree in worktrees),
        encoding="utf-16",
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Run a Fun Time session from a branch worktree.")
    ap.add_argument("worktree", nargs="?", help="The worktree to run the session from.")
    ap.add_argument(
        "--list",
        nargs="?",
        const="-",
        metavar="FILE",
        help="List the worktrees available to verify, newest first — to FILE, or to stdout.",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # A commit subject — or a path — can carry characters the console codepage
    # has no room for, and the launcher redirects this console to a log file.
    # Mark those rather than let a print take the launch down.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if args.list:
        worktrees = list_worktrees()
        if args.list == "-":
            for worktree in worktrees:
                print(f"{worktree.path}{FIELD_SEPARATOR}{worktree.label}")
        else:
            write_worktree_list(Path(args.list), worktrees)
        return 0
    if not args.worktree:
        parser.error("give the worktree to run a session from, or --list to see them")
    return launch(Path(args.worktree))


if __name__ == "__main__":
    raise SystemExit(main())
