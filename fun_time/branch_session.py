"""Run a real session out of a branch worktree, so work can be judged before it lands.

Fun Time runs from the primary checkout, which only moves when ``main`` does, so
a branch waiting on a pull request is code the user cannot see or judge.  The
project's CLAUDE.md says how an agent hands one over (``--shortcut``, then
``--remove-shortcut`` once it lands); this is what such a session IS.

**It replaces the live one; it never runs beside it.**  Nearly everything a
session touches is one-per-machine -- the ``#SingleInstance Force`` hotkey shell,
the three UDP endpoints and the loopback port, the microphone, the broker holding
the OSR2's serial port, the monitors.  The integration suite escapes that on a
hidden desktop with those endpoints stripped; a session watched on the real
screen cannot.  So two sessions are made impossible instead: the generated config
carries the live session's ``instance_id``, so both take the *same* mutex and
whichever starts second is refused.

What a branch session gets of its own is ``state/`` -- command files, playlists,
logs, thumbnails, resume point -- so a half-finished branch cannot corrupt what
the live session reads back.  Everything else is deliberately the real thing,
because a verification run on fixtures verifies fixtures.  The broker's files are
the exception inside that: they live in ``state/`` but belong to ``../broker``,
which never learns a session moved, so they stay pinned to the primary's.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from app_support.dependencies import pinned_siblings
from app_support.subprocess_utils import hidden_subprocess_kwargs
from app_support.win32 import TaskbarApp, describe_taskbar_app

# PROJECT_DIR names whichever checkout imported the package, and this module is
# the one place that has to tell two checkouts apart — so it is read through the
# module at call time rather than bound once at import.
from . import config as config_module
from . import preview_marker
from .branch_seeding import mirror_private_overlays, seed_derived_caches
from .checkout_overrides import (
    GENAU_DIRS_OVERRIDE_NAME,
    ORIGENERATOR_DIR_OVERRIDE_NAME,
    STATE_DIRNAME,
    override_lines,
    primary_of,
)
from .child_launch import no_console_window
from .config import DEFAULT_CONFIG_PATH, ProjectConfig, load_config
from .project_paths import PROJECT_ICON
from .shortcuts import read_shortcuts, write_shortcut

# Written into the worktree's own state dir, which is git-ignored — this file
# holds the machine's real library paths and must never be committable.
BRANCH_CONFIG_NAME = "fun_time_branch_config.json"

OUT_OF_DATE_NOTE_NAME = "branch_out_of_date.txt"

# The shared launcher every generated shortcut points at, in the primary.
LAUNCHER_NAME = "launch_branch.vbs"

# Each generated launcher is "Verify <branch>.lnk", written in the worktree it
# runs.  ``*.lnk`` is git-ignored, which is what makes a checkout a safe place
# to leave one.
SHORTCUT_PREFIX = "Verify "
SHORTCUT_SUFFIX = ".lnk"

SHORTCUT_VR_INFIX = " in VR"
VR_LAUNCH_FLAG = "--vr"
VR_ICON_NAME = "vr_icon.ico"
DESKTOP_ICON_NAME = "icon.ico"

RESERVED_IN_FILENAMES = r'[<>:"/\|?*]'

FIELD_SEPARATOR = "\t"
DETACHED = "(detached)"

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
        **hidden_subprocess_kwargs(),
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


def _primary_resolved_values(real: ProjectConfig) -> dict[str, dict[str, object]]:
    """Every raw config value the loader resolves against its own checkout,
    paired with what it resolved to in the primary.

    ``config._resolve_path`` anchors a relative value on ``config.PROJECT_DIR``,
    whichever checkout imported the package -- so the same config file read from
    a worktree names files *inside that worktree*.  The live config leans on that
    for ``favs.csv`` (which would come up empty), the broker's launcher (a
    directory above the primary, which from a worktree is nothing), and the
    Chrome shortcut, untracked and so in no worktree.

    ``broker_state_dir`` is pinned for a different reason: it defaults to
    ``state_dir``, which is the one value this rewrite goes on to move.  The
    broker is the machine's one broker, configured against the primary for good,
    so a branch session whose broker files followed that move would read an empty
    directory -- the console's broker light red and its OSR2 light "off" while
    the device is plainly running.

    Every value comes from *real*, the config as production loaded it.
    ``state_dir`` is listed so this is the complete set, its being overridden
    afterwards an exception on purpose rather than an omission.
    """
    paths, browser, regen = real.paths, real.random_favs_browser, real.regen
    return {
        "paths": {
            "ahk_exe": paths.ahk_exe,
            "python_exe": paths.python_exe,
            "main_player_library_dirs": paths.main_player_library_dirs,
            "portrait_dirs": paths.portrait_dirs,
            "landscape_dirs": paths.landscape_dirs,
            "weird_dir": paths.weird_dir,
            "clips_dir": paths.clips_dir,
            "audio_dir": paths.audio_dir,
            "favs_file": paths.favs_file,
            "state_dir": paths.state_dir,
            "broker_state_dir": paths.broker_state_dir,
            "genau_python_exe": paths.genau_python_exe,
            "genau_config_path": paths.genau_config_path,
            "genau_project_dirs": paths.genau_project_dirs,
            "broker_tray_launcher": paths.broker_tray_launcher,
            "origenerator_dir": paths.origenerator_dir,
            "origenerator_python_exe": paths.origenerator_python_exe,
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


def _apply_genau_checkout_override(raw: dict, state_dir: Path) -> None:
    """Let a worktree say for itself which checkout of ../genau its session runs.

    ``paths.genau_project_dirs`` answers a per-SESSION question — the main player and Genau
    are launched with these directories in front of their venv's install — but it
    could only be said in the machine's one ``fun_time_config.json``, which every
    session reads.  A pin written there for one agent's genau branch reached the
    user's ordinary session and every other agent's, each silently running an
    unlanded branch of another repo, and nothing ever took it back out.
    """
    lines = override_lines(state_dir / GENAU_DIRS_OVERRIDE_NAME)
    if lines is not None:
        raw.setdefault("paths", {})["genau_project_dirs"] = lines


def _apply_origenerator_checkout_override(raw: dict, state_dir: Path) -> None:
    """The same, for the Origenerator checkout a session hosts."""
    lines = override_lines(state_dir / ORIGENERATOR_DIR_OVERRIDE_NAME)
    if lines is not None:
        raw.setdefault("paths", {})["origenerator_dir"] = lines[0] if lines else ""


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

    Everything else rides through verbatim — including keys *this* code has
    never heard of, which matters because ``launch_branch.vbs`` runs the primary
    checkout's copy of this module against a worktree's session.  A branch that
    starts reading a new config key is therefore launched by a generator that
    does not pin it: the key reaches the session only if the machine's own
    ``fun_time_config.json`` already names it, absolutely.
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
    _apply_genau_checkout_override(raw, state_dir)
    _apply_origenerator_checkout_override(raw, state_dir)

    mirror_private_overlays(primary, worktree)

    state_dir.mkdir(parents=True, exist_ok=True)
    seed_derived_caches(real.paths.state_dir, state_dir)

    destination = state_dir / BRANCH_CONFIG_NAME
    destination.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    return destination


ORCHESTRATOR_MODULES = {False: "fun_time.orchestrator", True: "fun_time_vr.orchestrator"}


def describe_the_session_on_the_taskbar(worktree: Path) -> None:
    shown = preview_marker.shown_as()
    if shown is None:
        return
    describe_taskbar_app(preview_marker.session_identity(shown), TaskbarApp(
        name=preview_marker.app_title(shown),
        icon=preview_marker.icon_file(PROJECT_ICON, shown),
        relaunch=subprocess.list2cmdline(
            ["wscript.exe", str(primary_of(worktree) / LAUNCHER_NAME), str(worktree)]),
    ))


def launch(worktree: Path, *, vr: bool = False, primary: Path | None = None,
           **kwargs) -> int:
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
    primary = (primary or primary_checkout()).resolve()
    missing = commits_missing(worktree, primary)
    if missing:
        return _refuse(worktree, branch_label(worktree, current_branch(worktree)),
                       _older_than_the_fun_time_he_runs(missing),
                       str(OutOfDateWorktree(worktree, missing)))
    older = sibling_checkouts_older_than_the_pin(worktree)
    if older:
        return _refuse(worktree, branch_label(worktree, current_branch(worktree)),
                       PLAYERS_FROM_BEFORE_THE_PIN, str(OutOfDateSibling(older[0])))
    config_path = build_branch_config(worktree, primary=primary, **kwargs)
    command = [sys.executable, "-m", ORCHESTRATOR_MODULES[vr], "--config", str(config_path)]
    print(f"Running {subprocess.list2cmdline(command)}\n  in {worktree}", flush=True)
    return subprocess.run(
        command, cwd=str(worktree), check=False, env={**os.environ, preview_marker.FLAG: "1"},
        **no_console_window()).returncode


def _older_than_the_fun_time_he_runs(missing: int) -> str:
    return (f"That branch's copy of Fun Time is {missing} change{'' if missing == 1 else 's'} "
            "older than the one you normally run, and a branch that old can start things this "
            "computer no longer has.")


def _carries(checkout: Path, commit: str) -> bool:
    return subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=str(checkout),
        capture_output=True, **hidden_subprocess_kwargs()).returncode == 0


@dataclass(frozen=True)
class SiblingOlderThanThePin:
    checkout: Path
    repo: str
    tag: str


class OutOfDateSibling(RuntimeError):
    def __init__(self, older: SiblingOlderThanThePin):
        super().__init__(
            f"{older.checkout} does not carry {older.tag}, the {older.repo} this branch is "
            "built against, so a Main Player run out of it would lack what the branch imports. "
            f"Rebase that checkout onto {older.repo}'s origin/main, or take it out of "
            f"state/{GENAU_DIRS_OVERRIDE_NAME}, first.")


PLAYERS_FROM_BEFORE_THE_PIN = (
    "That branch is set to run its players out of a player_core checkout older than the "
    "one it was built against, so its Main player would not come up.")


def sibling_checkouts_older_than_the_pin(worktree: Path) -> list[SiblingOlderThanThePin]:
    pyproject = worktree / "pyproject.toml"
    if not pyproject.is_file():
        return []
    pins = pinned_siblings(pyproject)
    older = []
    for entry in override_lines(worktree / STATE_DIRNAME / GENAU_DIRS_OVERRIDE_NAME) or []:
        checkout = Path(entry)
        repo = _family_repo_of(checkout)
        if repo in pins and not _carries(checkout, pins[repo]):
            older.append(SiblingOlderThanThePin(checkout, repo, pins[repo]))
    return older


def _family_repo_of(checkout: Path) -> str | None:
    try:
        with (checkout / "pyproject.toml").open("rb") as handle:
            name = tomllib.load(handle).get("project", {}).get("name")
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return name.replace("-", "_") if name else None


def _refuse(worktree: Path, label: str, why_he_reads: str, what_the_agent_does: str) -> int:
    _leave_note(worktree, label, why_he_reads)
    print(f"Refused: {what_the_agent_does}", flush=True)
    return 1


def _leave_note(worktree: Path, label: str, why_it_could_not_start: str) -> None:
    state_dir = worktree / STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / OUT_OF_DATE_NOTE_NAME).write_text(
        f"Fun Time couldn't start on {label}.\n\n"
        f"{why_it_could_not_start}\n\n"
        f"Ask the session working on {label} to bring its branch up to date and make "
        "you a new launcher.\n",
        encoding="utf-8",
    )


def sibling_checkouts_line(
    worktree: Path, primary: Path, *, config_path: Path | None = None
) -> str:
    """One line saying which genau/player_core checkouts *worktree*'s next
    launch runs.

    Printed with every ``--shortcut``, because this part of the chain has no
    error state to fail loudly in: an override that is missing, empty, or
    naming a stale checkout silently runs some other genau, and the session
    then demonstrates code the branch never touched — he watches it and sees no
    difference.  Resolved exactly the way :func:`build_branch_config` will
    resolve it — the machine's config, then the worktree's own override on top
    (:func:`_apply_genau_checkout_override`) — so what this says is what the
    click will do.
    """
    config_path = config_path or (primary / DEFAULT_CONFIG_PATH.name)
    real = load_config(config_path, project_dir=primary)
    raw = {"paths": {
        "genau_project_dirs": [str(path) for path in real.paths.genau_project_dirs],
        "origenerator_dir": str(real.paths.origenerator_dir or ""),
    }}
    _apply_genau_checkout_override(raw, worktree / STATE_DIRNAME)
    _apply_origenerator_checkout_override(raw, worktree / STATE_DIRNAME)
    dirs = raw["paths"]["genau_project_dirs"]
    genau_line = (
        "genau_project_dirs: (empty — Genau, the main player and player_core run "
        "from their venv installs, the primaries)"
        if not dirs else "genau_project_dirs: " + os.pathsep.join(dirs)
    )
    origenerator = raw["paths"]["origenerator_dir"]
    origenerator_line = (
        "origenerator_dir: (none — the session hosts no Origenerator)"
        if not origenerator else f"origenerator_dir: {origenerator}"
    )
    return f"{genau_line}\n{origenerator_line}"


def branch_label(worktree: Path, branch: str) -> str:
    return worktree.name if branch == DETACHED else branch


def current_branch(worktree: Path) -> str:
    """The branch *worktree* has checked out, or :data:`DETACHED`."""
    name = _git(["rev-parse", "--abbrev-ref", "HEAD"], worktree).strip()
    return DETACHED if name == "HEAD" else name


def shortcut_name(worktree: Path, branch: str, *, vr: bool = False) -> str:
    """What the generated launcher for *worktree* is called in Explorer.

    The branch name, because that is what an agent tells him it made — a
    worktree's directory name is a slug he has never seen.  Slashes and the
    other characters Windows reserves become dashes, a worktree on no branch at
    all falls back to its directory, and a *vr* one says so.
    """
    stem = re.sub(RESERVED_IN_FILENAMES, "-", branch_label(worktree, branch)).strip()
    return f"{SHORTCUT_PREFIX}{stem}{SHORTCUT_VR_INFIX if vr else ''}{SHORTCUT_SUFFIX}"


def _generated_shortcuts(folder: Path) -> dict[Path, Path]:
    """The launchers this module wrote in *folder*, mapped to the worktree each
    one runs.  A folder can be full of his own files, so a name proves nothing:
    ownership is the arguments naming the branch launcher."""
    owned: dict[Path, Path] = {}
    found = read_shortcuts(folder, pattern=f"{SHORTCUT_PREFIX}*{SHORTCUT_SUFFIX}")
    for path, shortcut in found.items():
        tokens = [token.strip('"')
                  for token in shlex.split(shortcut.arguments or "", posix=False)]
        if len(tokens) >= 2 and Path(tokens[0]).name.lower() == LAUNCHER_NAME:
            owned[path] = Path(tokens[1])
    return owned


def prune_stale_shortcuts(primary: Path) -> list[Path]:
    """Delete the launchers left in *primary* whose worktree is gone; return which.

    Nothing writes there any more -- a launcher lives in the worktree it runs,
    and goes when that goes -- but the ones written before that still sit in his
    Fun Time folder, and a worktree going is the only thing that can say one is
    finished with.  Run whenever a new launcher is written, until the folder is
    empty of them.
    """
    removed: list[Path] = []
    for path, worktree in sorted(_generated_shortcuts(primary).items()):
        if not worktree.is_dir():
            path.unlink()
            removed.append(path)
    return removed


def commits_missing(worktree: Path, primary: Path) -> int:
    primary_head = _git(["rev-parse", "HEAD"], primary).strip()
    return int(_git(["rev-list", "--count", f"HEAD..{primary_head}"], worktree).strip())


class OutOfDateWorktree(RuntimeError):
    def __init__(self, worktree: Path, missing: int):
        super().__init__(
            f"{worktree} is missing {missing} commit{'' if missing == 1 else 's'} the primary "
            "checkout has, so its launcher can break before it is clicked. Rebase it onto "
            "origin/main first."
        )


def write_launch_shortcut(
    worktree: Path, *, primary: Path | None = None, vr: bool = False
) -> Path:
    """Put a launcher for *worktree* in *worktree*, and return where.

    This is how a branch reaches him: an agent makes one and hands him the
    one-click link to it.  Nothing to choose — the branch is baked in — and it
    points at ``launch_branch.vbs`` in the primary rather than carrying the
    launch, so one made weeks ago still runs today's.

    It lives in the worktree because he never goes looking for the file, and
    because every launcher in one shared folder is a launcher in every other
    agent's tidiness check: each of them found launchers that were not theirs to
    speak for, and told him about them.  Here it belongs to one branch, is
    invisible to every other, and goes when that branch's worktree goes.
    """
    primary = (primary or primary_checkout()).resolve()
    worktree = worktree.resolve()
    launcher = primary / LAUNCHER_NAME
    if not launcher.is_file():
        raise FileNotFoundError(
            f"{launcher} is missing — the primary checkout has to be on a main that "
            "carries the branch launcher before a shortcut to it can run."
        )
    missing = commits_missing(worktree, primary)
    if missing:
        raise OutOfDateWorktree(worktree, missing)
    older = sibling_checkouts_older_than_the_pin(worktree)
    if older:
        raise OutOfDateSibling(older[0])
    branch = current_branch(worktree)
    destination = worktree / shortcut_name(worktree, branch, vr=vr)
    arguments = [str(launcher), str(worktree), branch]
    if vr:
        arguments.append(VR_LAUNCH_FLAG)
    write_shortcut(
        destination,
        # wscript rather than the .vbs itself: a shortcut's target has to be an
        # executable for arguments to reach the script.
        target=str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wscript.exe"),
        arguments=subprocess.list2cmdline(arguments),
        working_dir=str(primary),
        icon=str(primary / (VR_ICON_NAME if vr else DESKTOP_ICON_NAME)),
        description=f"Run Fun Time{' VR' if vr else ''} on {branch}",
    )
    prune_stale_shortcuts(primary)
    return destination


def remove_launch_shortcut(worktree: Path, *, primary: Path | None = None) -> list[Path]:
    """Take *worktree*'s launcher back out; return the ones removed.

    An agent's last step once its work has landed: the branch is in Fun Time by
    then, so a launcher still offering to run it separately can only confuse.

    His Fun Time folder is looked in as well as the worktree, because an agent
    that made its launcher under the old placement still has to take that one
    out.  Matched by the worktree the shortcut runs rather than by its name, so
    a branch renamed since makes no difference and both flavours go.  Run it
    before removing the worktree: from a gone directory there is no package
    left to run it with.
    """
    primary = (primary or primary_checkout()).resolve()
    worktree = worktree.resolve()
    removed: list[Path] = []
    for folder in (worktree, primary):
        for path, target in _generated_shortcuts(folder).items():
            if target == worktree:
                path.unlink()
                removed.append(path)
    prune_stale_shortcuts(primary)
    return removed


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Run a Fun Time session from a branch worktree.")
    ap.add_argument("worktree", nargs="?", help="The worktree to run the session from.")
    ap.add_argument(
        "--shortcut",
        nargs="?",
        const=".",
        metavar="WORKTREE",
        help="Write WORKTREE's launcher (default: this checkout's) into WORKTREE, "
             "print its path, and exit.",
    )
    ap.add_argument(
        "--remove-shortcut",
        nargs="?",
        const=".",
        metavar="WORKTREE",
        help="Take WORKTREE's launcher (default: this checkout's) back out once its "
             "work has landed, and exit.",
    )
    ap.add_argument(
        "--vr",
        action="store_true",
        help="Aim at the headset: FunTimeVR's orchestrator instead of the desktop one, "
             "and a launcher named for it. Applies to --shortcut and to running a session.",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # A path can carry characters the console codepage has no room for, and the
    # launcher redirects this console to a log file.  Mark those rather than let
    # a print take the launch down.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    if args.shortcut:
        target = config_module.PROJECT_DIR if args.shortcut == "." else Path(args.shortcut)
        try:
            print(write_launch_shortcut(target, vr=args.vr))
        except (OutOfDateWorktree, OutOfDateSibling) as refused:
            print(f"No launcher written: {refused}", file=sys.stderr)
            return 1
        print(sibling_checkouts_line(target, primary_checkout()))
        return 0
    if args.remove_shortcut:
        target = (
            config_module.PROJECT_DIR if args.remove_shortcut == "." else Path(args.remove_shortcut)
        )
        removed = remove_launch_shortcut(target)
        for path in removed:
            print(path)
        if not removed:
            print(f"No launcher was there for {target}")
        return 0
    if not args.worktree:
        parser.error("give the worktree to run a session from, or --shortcut to make its launcher")
    return launch(Path(args.worktree), vr=args.vr)


if __name__ == "__main__":
    raise SystemExit(main())
