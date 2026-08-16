"""Give every process Fun Time launches a name of its own in the task list.

Windows fixes a process's image name at ``CreateProcess`` time, so every child
started through a venv's ``pythonw.exe`` arrives in Task Manager as one more
anonymous ``pythonw.exe`` -- indistinguishable from the user's other Python
apps.  That is not a cosmetic problem.  When a session strands a child (an
orchestrator that dies without reaping leaves its companions running, and no
window is left to close), the only way back is the task list, and the task list
offered a column of identical "Python" rows with no way to tell Fun Time's from
the ones that would break something else if killed.

The image name is the one thing the task list shows that a launcher gets to
choose, so we choose it: each child is started through a copy of the
interpreter named for the role it is about to run --
``FunTime-AudioCompanion.exe``, ``FunTime-Portrait.exe``.  Anything named
``FunTime-*`` is ours and is safe to end; anything still called ``python.exe``
is somebody else's.

Two details of *which* interpreter gets copied *where* are load-bearing, and
both were settled by trying the alternative:

  * The copy stays in the venv's ``Scripts`` directory.  Python finds
    ``pyvenv.cfg`` one level up from there, so the copy is the same venv with
    the same ``site-packages``.
  * What gets copied is that directory's launcher, NOT the base interpreter it
    redirects to.  Dropping a copy of the base ``python.exe`` into ``Scripts``
    also resolves the venv, and it has the appeal of running as a single
    process -- but it loses the DLL search path PyQt6 needs and dies on
    ``import QtGui``, which is every Qt window this app owns.

Copying the launcher means the real interpreter still runs as a child named
``python.exe``, so a named parent has an anonymous worker under it.  That is
the cost of the approach, and it is worth paying: the launcher holds its child
in a job object, so killing the named parent takes the worker with it -- which
is the whole point of being able to find the named parent.

Naming a process is never worth failing a launch over, so every failure here
falls back to the interpreter we were handed and the session comes up exactly
as it did before.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# What marks an image name as one of ours.  Every role-named copy carries it, so
# a task list sorted by name gathers the whole session under one heading, and a
# sweep can bound itself to Fun Time without knowing the roles.
EXE_PREFIX = "FunTime-"

# The image names a Fun Time child can run under: a role-named copy, or -- when
# the copy could not be made, or for a child launched before this module was
# reached -- the plain interpreter it falls back to.  Written for PowerShell's
# ``-match``, which is where every process sweep in this repo applies it, and
# anchored so it cannot catch some other app's ``mypythonw.exe``.
PROCESS_NAME_PATTERN = r"^pythonw?\.exe$|^py\.exe$|^" + EXE_PREFIX + r"[A-Za-z]+\.exe$"

_ROLE_RE = re.compile(r"^[A-Za-z]+$")


def role_exe_name(python_exe: str | Path, role: str) -> str:
    """The file name a *role*'s copy of *python_exe* gets.

    The suffix comes from the interpreter rather than being hardcoded, so a
    console child copied from ``python.exe`` and a windowed one copied from
    ``pythonw.exe`` both keep the console behavior they were launched for --
    naming a process must not decide whether it has a console.
    """
    if not _ROLE_RE.match(role):
        raise ValueError(f"role must be plain letters, got {role!r}")
    return f"{EXE_PREFIX}{role}{Path(python_exe).suffix}"


def is_fun_time_exe_name(name: str) -> bool:
    """Whether an image name is one of the role-named copies this module makes."""
    return name.lower().startswith(EXE_PREFIX.lower()) and name.lower().endswith(".exe")


def _is_current(copy: Path, source: Path) -> bool:
    """Whether *copy* is still a faithful copy of *source*.

    Size and mtime rather than a hash: the question is only whether the
    interpreter was upgraded under us, and a rewrite of the same bytes would be
    harmless anyway.  Cheap enough to ask on every launch, which is what keeps a
    stale copy from outliving a Python upgrade.
    """
    if not copy.is_file():
        return False
    try:
        copy_stat = copy.stat()
        source_stat = source.stat()
    except OSError:
        return False
    # Two seconds of slack on the timestamp: ``copy2`` carries the source mtime
    # over, but filesystems disagree about how finely they store it, and an exact
    # comparison that loses by a rounding tick declares every copy stale and
    # rewrites a running image on every single launch.
    return (copy_stat.st_size == source_stat.st_size
            and abs(copy_stat.st_mtime - source_stat.st_mtime) <= 2)


def identified_python_exe(python_exe: str | Path, role: str) -> str:
    """Return an interpreter that will run *role* under a name naming Fun Time.

    Makes the role-named copy beside *python_exe* if it is missing or stale, and
    returns it.  Returns *python_exe* unchanged if the copy cannot be made --
    a read-only venv, an antivirus hold, a disk that is full.  The session then
    runs exactly as it did before this module existed, with anonymous children,
    which is a worse task list and a working app.
    """
    source = Path(python_exe)
    try:
        target = source.with_name(role_exe_name(source, role))
    except ValueError:
        logger.warning("Not naming the %s process: bad role", role, exc_info=True)
        return str(python_exe)

    if _is_current(target, source):
        return str(target)

    try:
        # copyfile rather than copy2, which treats a directory destination as
        # "copy INTO this" -- so something of our name that is not a file (a
        # directory left by a botched cleanup) made the copy report success and
        # handed the launcher a directory to run.  copyfile refuses it instead.
        # copystat then carries the mtime across, which is what _is_current
        # compares against; without it every launch sees its own copy as stale
        # and rewrites it.
        shutil.copyfile(source, target)
        shutil.copystat(source, target)
    except OSError:
        # A copy already there but not current is still a working interpreter of
        # the right kind, and one Python upgrade behind at worst -- better than
        # dropping the name entirely.  The usual reason to land here is that the
        # last session's copy is still running and Windows will not overwrite a
        # running image.  ``is_file`` rather than ``exists``: something of that
        # name which is not a file (a directory left by a botched cleanup) is why
        # the copy failed, and handing it back as an interpreter fails the launch
        # outright -- the one outcome this fallback exists to avoid.
        if target.is_file():
            logger.info("Kept the existing %s launcher; could not refresh it", target.name)
            return str(target)
        logger.warning("Not naming the %s process; launching unnamed", role, exc_info=True)
        return str(python_exe)
    return str(target)
