"""Run a real session out of a branch worktree, so work can be judged before it lands.

Fun Time runs from the primary checkout, and that checkout only moves when
``main`` does — so a branch waiting on a pull request is code the user cannot
see, run or judge.  This is the third option between parking the branch and
landing it unverified: point a real session at the unlanded worktree, on the
real library, on the real monitors, first.

The user is never asked to find a branch.  An agent with something to show runs
``python -m fun_time.branch_session --shortcut`` from its worktree, which leaves
a ``Verify <branch>.lnk`` in the primary checkout — the folder he keeps open —
and tells him that filename.  He double-clicks it; the branch is already baked
in.  ``launch_branch.vbs`` is the launcher every one of those shortcuts points
at, and is not run on its own.  Once the work lands, the same agent runs
``--remove-shortcut`` to take its file back out again: what is sitting in that
folder should be what is waiting on him, and nothing else.

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

The broker's own files are the exception inside that exception.  They live in
``state/`` too, but they belong to ``../broker``, which opens them from one
directory named in its own config and never learns a session moved — so they
stay pinned to the primary's (``paths.broker_state_dir``) while everything else
in ``state/`` moves into the worktree.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

# PROJECT_DIR names whichever checkout imported the package, and this module is
# the one place that has to tell two checkouts apart — so it is read through the
# module at call time rather than bound once at import.
from . import config as config_module
from .config import DEFAULT_CONFIG_PATH, ProjectConfig, load_config

# Written into the worktree's own state dir, which is git-ignored — this file
# holds the machine's real library paths and must never be committable.
BRANCH_CONFIG_NAME = "fun_time_branch_config.json"

# The shared launcher every generated shortcut points at, in the primary.
LAUNCHER_NAME = "launch_branch.vbs"

# Each generated launcher is "Verify <branch>.lnk", written beside launch.vbs in
# the primary checkout — the folder he already keeps open.  ``*.lnk`` is
# git-ignored, which is what makes a checkout a safe place to leave them.
SHORTCUT_PREFIX = "Verify "
SHORTCUT_SUFFIX = ".lnk"

SHORTCUT_VR_INFIX = " in VR"
VR_LAUNCH_FLAG = "--vr"
VR_ICON_NAME = "vr_icon.ico"
DESKTOP_ICON_NAME = "icon.ico"

RESERVED_IN_FILENAMES = r'[<>:"/\|?*]'

STATE_DIRNAME = "state"
FIELD_SEPARATOR = "\t"
DETACHED = "(detached)"

# A worktree's own answer to "which checkout of ../genau do Nau and Genau run
# out of" — one absolute path per line, in the worktree's state dir, blank lines
# and #-comments ignored.  See :func:`_apply_genau_checkout_override`.
GENAU_DIRS_OVERRIDE_NAME = "genau_project_dirs.txt"

# The same per-worktree answer for "which Origenerator checkout does this
# session host": one absolute path (or an empty file for none at all), for the
# same reason genau's exists — the machine's one config must not be repointed
# at an unlanded branch.  See :func:`apply_origenerator_dir_override`.
ORIGENERATOR_DIR_OVERRIDE_NAME = "origenerator_dir.txt"

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

    ``broker_state_dir`` is pinned for a different reason, and is the one that
    matters most here: it defaults to ``state_dir``, and ``state_dir`` is the one
    value this rewrite goes on to move.  The broker is the machine's one broker,
    configured against the primary for good, so a branch session that let its
    heartbeat, serial-activity, command and mode files follow that move would read
    an empty directory — the main console's broker light red and its OSR2 light
    "off" while the device is plainly running, and its park and resume verbs
    written where nothing reads them.

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

    ``paths.genau_project_dirs`` answers a per-SESSION question — Nau and Genau
    are launched with these directories in front of their venv's install, so this
    is which checkout of that repo they are — but it could only be said in the
    machine's one ``fun_time_config.json``, which every session on the machine
    reads.  So an agent judging a genau branch wrote its worktree there, and that
    pin then reached the user's ordinary ``launch.vbs`` session and every *other*
    agent's branch session too, each of them silently running an unlanded branch
    of another repo.  Nothing took it back out either: the launcher it pairs with
    has ``--remove-shortcut`` and this had no counterpart at all.  It cost a whole
    round trip when a fun_time branch could not show a console button that had
    landed in genau, because the pinned checkout predated it.

    A file in the worktree's own state dir answers it per session instead: one
    absolute path per line, ``#`` comments and blank lines ignored.  Present, it
    REPLACES the machine's value outright — so an empty file is the way to say
    "the plain venv install, whatever the machine is pinned to", which is what a
    branch that has nothing to do with genau wants.  Absent, the machine's value
    rides through as before.  It lives beside the branch config, is git-ignored
    with the rest of ``state/``, and dies with the worktree.
    """
    override = state_dir / GENAU_DIRS_OVERRIDE_NAME
    try:
        text = override.read_text(encoding="utf-8")
    except OSError:
        return
    dirs = [line.strip() for line in text.splitlines()]
    raw.setdefault("paths", {})["genau_project_dirs"] = [
        line for line in dirs if line and not line.startswith("#")
    ]


def _apply_origenerator_checkout_override(raw: dict, state_dir: Path) -> None:
    """Let a worktree say for itself which Origenerator checkout its session hosts.

    Same shape and same reason as :func:`_apply_genau_checkout_override`: the
    config key is per-machine but the question is per-SESSION, so an agent
    judging an origenerator branch writes the worktree path here rather than
    into the machine's one ``fun_time_config.json``.  Present, the file
    REPLACES the machine's value — one absolute path, or empty to host none at
    all (which is how a branch unrelated to origenerator gets the plain
    machine setup even while some other agent's pin sits in the config).
    Absent, the machine's value rides through.
    """
    override = state_dir / ORIGENERATOR_DIR_OVERRIDE_NAME
    try:
        text = override.read_text(encoding="utf-8")
    except OSError:
        return
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.strip().startswith("#")]
    raw.setdefault("paths", {})["origenerator_dir"] = lines[0] if lines else ""


def apply_origenerator_dir_override(config):
    """This checkout's origenerator override, applied to a loaded config.

    The branch-config generator runs the PRIMARY checkout's copy of this
    module (see :func:`build_branch_config`), so a branch that INTRODUCES the
    override cannot rely on the generator applying it — the orchestrator calls
    this at launch instead, resolving the file against its own checkout.  A
    no-op wherever the override file does not exist, which is every ordinary
    session.

    Never in an integration run: the run's config decides what it hosts —
    isolation strips the key so nothing is hosted, and the origenerator-mode
    test then names its own fabricated stub.  This override once out-ranked
    both, and every session the suite launched from a worktree carrying the
    file quietly hosted the REAL app instead: the machine's one ComfyUI,
    booted on the hidden desktop by a test run.
    """
    if os.environ.get("FUN_TIME_RUN_INTEGRATION") == "1":
        return config
    override = config_module.PROJECT_DIR / STATE_DIRNAME / ORIGENERATOR_DIR_OVERRIDE_NAME
    try:
        text = override.read_text(encoding="utf-8")
    except OSError:
        return config
    lines = [line.strip() for line in text.splitlines()
             if line.strip() and not line.strip().startswith("#")]
    from dataclasses import replace as dc_replace
    new_dir = Path(lines[0]) if lines else None
    return dc_replace(config, paths=dc_replace(config.paths, origenerator_dir=new_dir))


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


# Nau's per-video durations, and the one thing here that is merged rather than
# copied.  Startup waits for Nau to report the video it is opening, Nau reports
# nothing until it has a duration for it, and against a cold cache it goes off
# and probes the whole library first — measured at 20s where a warm one is 0.1s.
DURATION_CACHE_NAME = "nau_durations.json"


def _seeded_state_names() -> tuple[str, ...]:
    """What a branch session starts from the live session's state rather than
    building for itself.

    Each of these is written into a session's state dir but describes the
    *library*, not the session, so a fresh state dir means redoing work already
    done against a library of thousands of files.  These two are cost paid after
    startup rather than during it: without them the HUD maps fill in one decoded
    frame at a time and the breeding view comes up empty — a wrongness the branch
    did not cause, which is exactly what a verification session must never show.
    The duration cache, which is the one that costs *startup*, is merged instead
    (see :func:`merge_duration_cache`).

    Nothing describing the *session* is here.  Playlists, command files, the
    resume point and the shared state stay the branch's own; sharing those is
    what the separate state dir exists to prevent.
    """
    from .thumbnail_cache import THUMBNAIL_CACHE_DIRNAME  # pulls in cv2

    return ("watch_stats.json", THUMBNAIL_CACHE_DIRNAME)


def merge_duration_cache(live_state: Path, branch_state: Path) -> int:
    """Union the live session's durations into the branch's; return the total.

    Copying this one was wrong, and copy-if-newer — what it was — was wrong in
    the way that hides: Nau rewrites the file with exactly what it loaded plus
    what it probed, so a branch session's copy shrinks to its own view of the
    library and is then *newer* than the live session's.  Every launch after the
    first therefore skipped the seed, kept the small file, and re-probed
    whatever the library had churned since — which is why branch launches went
    on taking half a minute after the seeding landed.

    The two files are partial views of one library, so the union is strictly
    better than either.  The branch's own readings win on conflict, being the
    more recent observation of that file; everything the live session knows and
    the branch does not comes across.  A stale entry costs nothing either way —
    every entry is validated against the file's mtime and size before it is
    trusted, and re-probed when it does not match.
    """
    live = _read_json_dict(live_state / DURATION_CACHE_NAME)
    branch = _read_json_dict(branch_state / DURATION_CACHE_NAME)
    merged = {**live, **branch}
    if merged and merged != branch:
        branch_state.mkdir(parents=True, exist_ok=True)
        (branch_state / DURATION_CACHE_NAME).write_text(json.dumps(merged), encoding="utf-8")
    return len(merged)


def _read_json_dict(path: Path) -> dict:
    """*path* as a dict, or empty when it is missing, unreadable or not one."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def seed_derived_caches(live_state: Path, branch_state: Path) -> list[Path]:
    """Start the library-derived caches from *live_state*; return what landed.

    Copied rather than hardlinked, and never written back: a branch is
    unfinished code, and the live session's caches are not its to corrupt.  For
    the copied ones a file already in the branch state dir and newer than the
    live one is left alone — that is the branch session's own work, and this is
    a seed rather than a sync.  The duration cache cannot work that way and is
    merged; :func:`merge_duration_cache` says why.
    """
    seeded: list[Path] = []
    if merge_duration_cache(live_state, branch_state):
        seeded.append(branch_state / DURATION_CACHE_NAME)
    for name in _seeded_state_names():
        source = live_state / name
        if source.is_dir():
            seeded.extend(_seed_directory(source, branch_state / name))
        elif source.is_file() and _seed_file(source, branch_state / name):
            seeded.append(branch_state / name)
    return seeded


def _seed_file(source: Path, destination: Path) -> bool:
    if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return True


def _seed_directory(source: Path, destination: Path) -> list[Path]:
    """Copy the entries *destination* does not have yet.

    Only the missing ones: a cache keyed by content — a thumbnail is named for
    its video and that video's modification time — never needs refreshing, so
    every launch after the first copies nothing.
    """
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_file() and not target.exists():
            shutil.copyfile(entry, target)
            copied.append(target)
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


def launch(worktree: Path, *, vr: bool = False, **kwargs) -> int:
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
    command = [sys.executable, "-m", ORCHESTRATOR_MODULES[vr], "--config", str(config_path)]
    print(f"Running {subprocess.list2cmdline(command)}\n  in {worktree}", flush=True)
    return subprocess.run(command, cwd=str(worktree), check=False).returncode


def apply_genau_dirs_to_sys_path() -> list[str]:
    """Put this checkout's genau_project_dirs override on ``sys.path``.

    The override reaches Genau and Nau as subprocess PYTHONPATH, but the
    orchestrator's own process — and the device arbiter inside it — resolves
    ``player_core`` through the venv, which is the primary checkout's.  A
    branch that leans on an unlanded player_core change therefore imports
    names the primary does not have yet, and the session dies at launch.
    Called at the top of the orchestrator, ahead of the bridge imports, so a
    verification session runs the same player_core its Genau and Nau do; a
    no-op wherever the override file does not exist, which is every ordinary
    session.  Returns what it added.
    """
    override = config_module.PROJECT_DIR / STATE_DIRNAME / GENAU_DIRS_OVERRIDE_NAME
    try:
        text = override.read_text(encoding="utf-8")
    except OSError:
        return []
    dirs = [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
            and Path(line.strip()).is_dir()]
    for entry in reversed(dirs):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return dirs


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
        "genau_project_dirs: (empty — Genau, Nau and player_core run "
        "from their venv installs, the primaries)"
        if not dirs else "genau_project_dirs: " + os.pathsep.join(dirs)
    )
    origenerator = raw["paths"]["origenerator_dir"]
    origenerator_line = (
        "origenerator_dir: (none — the session hosts no Origenerator)"
        if not origenerator else f"origenerator_dir: {origenerator}"
    )
    return f"{genau_line}\n{origenerator_line}"


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
    name = worktree.name if branch == DETACHED else branch
    stem = re.sub(RESERVED_IN_FILENAMES, "-", name).strip()
    return f"{SHORTCUT_PREFIX}{stem}{SHORTCUT_VR_INFIX if vr else ''}{SHORTCUT_SUFFIX}"


def _ps_quote(value: str) -> str:
    """*value* as a PowerShell single-quoted literal."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _powershell(script: str) -> str:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def _read_shortcuts(primary: Path) -> dict[Path, tuple[str, str]]:
    """Every ``Verify *.lnk`` sitting in *primary*, as (target, arguments).

    Read through PowerShell's ``WScript.Shell`` rather than pywin32, which this
    venv does not carry — ``windows_bridge_sequencer.resolve_shortcut`` keeps
    the same fallback for the same reason.  One invocation for the whole folder,
    because starting PowerShell costs far more than reading a shortcut does.
    """
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"Get-ChildItem -LiteralPath {_ps_quote(str(primary))} "
        f"-Filter {_ps_quote(f'{SHORTCUT_PREFIX}*{SHORTCUT_SUFFIX}')} -ErrorAction SilentlyContinue "
        "| ForEach-Object { $link = $shell.CreateShortcut($_.FullName); "
        "Write-Output ($_.FullName + \"`t\" + $link.TargetPath + \"`t\" + $link.Arguments) }"
    )
    found: dict[Path, tuple[str, str]] = {}
    for line in _powershell(script).splitlines():
        fields = line.split(FIELD_SEPARATOR)
        if len(fields) == 3:
            found[Path(fields[0])] = (fields[1], fields[2])
    return found


def _generated_shortcuts(primary: Path) -> dict[Path, Path]:
    """Those of them this module wrote, mapped to the worktree each one runs.

    A filename is not proof of anything — the folder is full of his own files —
    so ownership is decided by the arguments naming the branch launcher.
    """
    owned: dict[Path, Path] = {}
    for path, (_target, arguments) in _read_shortcuts(primary).items():
        tokens = [token.strip('"') for token in shlex.split(arguments or "", posix=False)]
        if len(tokens) >= 2 and Path(tokens[0]).name.lower() == LAUNCHER_NAME:
            owned[path] = Path(tokens[1])
    return owned


def prune_stale_shortcuts(primary: Path) -> list[Path]:
    """Delete the generated launchers whose worktree is gone; return which.

    A worktree is removed once its branch lands, and a shortcut still pointing
    at one is a file in his folder that can only fail.  Run whenever a new one
    is written, so what sits there is roughly what is in flight rather than
    everything ever verified — this repo carries dozens of worktrees, and
    without this the folder fills up within days.
    """
    removed: list[Path] = []
    for path, worktree in sorted(_generated_shortcuts(primary).items()):
        if not worktree.is_dir():
            path.unlink()
            removed.append(path)
    return removed


def write_launch_shortcut(
    worktree: Path, *, primary: Path | None = None, vr: bool = False
) -> Path:
    """Put a double-clickable launcher for *worktree* in the primary checkout.

    This is how a branch reaches him: an agent makes one of these, names the
    file, and he double-clicks it in the folder he already keeps open.  There is
    no menu and nothing to choose — the branch is baked into the shortcut, so
    the only thing he has to know is which file the agent told him about.

    It points at ``launch_branch.vbs`` in the primary rather than carrying the
    launch itself, so one made weeks ago still runs today's launcher.
    """
    primary = (primary or primary_checkout()).resolve()
    worktree = worktree.resolve()
    launcher = primary / LAUNCHER_NAME
    if not launcher.is_file():
        raise FileNotFoundError(
            f"{launcher} is missing — the primary checkout has to be on a main that "
            "carries the branch launcher before a shortcut to it can run."
        )
    branch = current_branch(worktree)
    destination = primary / shortcut_name(worktree, branch, vr=vr)
    arguments = [str(launcher), str(worktree), branch]
    if vr:
        arguments.append(VR_LAUNCH_FLAG)
    _write_shortcut(
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
    """Take *worktree*'s launcher back out of the primary checkout.

    An agent's last step once its work has landed: the branch is in Fun Time by
    then, so a shortcut still offering to run it separately is a file in his
    folder that can only confuse.  The stale sweep would catch it eventually,
    but only when some other agent happens to write a shortcut — which may be
    days away, and it is his folder in the meantime.

    Matched by the worktree the shortcut runs rather than by its name, so a
    branch renamed since makes no difference, and both flavours go.  Returns what
    was removed, empty if there was nothing.  Run it before removing the
    worktree: from a gone directory there is no package left to run it with.
    """
    primary = (primary or primary_checkout()).resolve()
    worktree = worktree.resolve()
    removed: list[Path] = []
    for path, target in _generated_shortcuts(primary).items():
        if target == worktree:
            path.unlink()
            removed.append(path)
    prune_stale_shortcuts(primary)
    return removed


def _write_shortcut(
    destination: Path, *, target: str, arguments: str, working_dir: str, icon: str, description: str
) -> None:
    """Write a .lnk through PowerShell's ``WScript.Shell``.

    There is no pure-Python way to author a shortcut, and pywin32 is not in this
    venv — the same reason ``resolve_shortcut`` reads them this way.
    """
    fields = {
        "TargetPath": target,
        "Arguments": arguments,
        "WorkingDirectory": working_dir,
        "IconLocation": icon,
        "Description": description,
    }
    assignments = "".join(f"$link.{name} = {_ps_quote(value)}; " for name, value in fields.items())
    _powershell(
        f"$link = (New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(destination))}); "
        f"{assignments}$link.Save()"
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Run a Fun Time session from a branch worktree.")
    ap.add_argument("worktree", nargs="?", help="The worktree to run the session from.")
    ap.add_argument(
        "--shortcut",
        nargs="?",
        const=".",
        metavar="WORKTREE",
        help="Write the double-clickable launcher for WORKTREE (default: this checkout) "
             "into the primary, print its path, and exit.",
    )
    ap.add_argument(
        "--remove-shortcut",
        nargs="?",
        const=".",
        metavar="WORKTREE",
        help="Take WORKTREE's launcher (default: this checkout's) back out of the primary "
             "once its work has landed, and exit.",
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
        print(write_launch_shortcut(target, vr=args.vr))
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
