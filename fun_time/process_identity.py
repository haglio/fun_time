"""Give every process Fun Time launches a name of its own in the task list.

Windows fixes a process's identity at ``CreateProcess`` time, so every child
started through a venv's ``pythonw.exe`` arrives as one more anonymous Python --
indistinguishable from the user's other Python apps.  That is not a cosmetic
problem.  When a session strands a child (an orchestrator that dies without
reaping leaves its companions running, and no window is left to close), the only
way back is the task list, and the task list offered a column of identical
"Python" rows with no way to tell Fun Time's from the ones that would break
something else if killed.

So each child is started through a copy of the interpreter, and the copy is made
to describe itself.  THREE things have to change together, because Windows shows
a process through three different fields and getting one is not enough:

  * the image name, which is the Details tab's Name column -- taken from the
    file, so the copy is called ``FunTime-Portrait.exe``;
  * the *file description*, which is what the Processes tab actually displays --
    taken from the exe's version resource, and the reason a first attempt that
    only renamed the file still showed a list of rows saying "Python";
  * the icon beside that row, likewise from the exe rather than from the window.

The version resource and the icon are rewritten into the copy with
``UpdateResource``.  Everything Fun Time launches ends up under a row reading
"Fun Time -- <what it is>" with Fun Time's mark on it, whatever the window
beneath it calls itself.

Two details of *which* interpreter gets copied *where* are load-bearing, and both
were settled by trying the alternative:

  * The copy stays in the venv's ``Scripts`` directory.  Python finds
    ``pyvenv.cfg`` one level up from there, so the copy is the same venv with
    the same ``site-packages``.
  * What gets copied is that directory's launcher, NOT the base interpreter it
    redirects to.  Dropping a copy of the base ``python.exe`` into ``Scripts``
    also resolves the venv, and it has the appeal of running as a single
    process -- but it loses the DLL search path PyQt6 needs and dies on
    ``import QtGui``, which is every Qt window this app owns.

Copying the launcher means the real interpreter still runs as a child named
``python.exe``, so a named parent has an anonymous worker under it.  That is the
cost of the approach, and it is worth paying: the launcher holds its child in a
job object, so killing the named parent takes the worker with it -- which is the
whole point of being able to find the named parent.

Naming a process is never worth failing a launch over, so every failure here
falls back to the interpreter we were handed and the session comes up exactly as
it did before.
"""
from __future__ import annotations

import ctypes
import logging
import re
import shutil
import struct
import sys
from ctypes import wintypes
from pathlib import Path

logger = logging.getLogger(__name__)

# What marks an image name as one of ours.  Every role-named copy carries it, so
# a task list sorted by name gathers the whole session under one heading, and a
# sweep can bound itself to Fun Time without knowing the roles.
EXE_PREFIX = "FunTime-"

# The name every row leads with, so they sort and read as one application.
APP_NAME = "Fun Time"

# The image names a Fun Time child can run under: a role-named copy, or -- when
# the copy could not be made, or for a child launched before this module was
# reached -- the plain interpreter it falls back to.  Written for PowerShell's
# ``-match``, which is where every process sweep in this repo applies it, and
# anchored so it cannot catch some other app's ``mypythonw.exe``.
PROCESS_NAME_PATTERN = r"^pythonw?\.exe$|^py\.exe$|^" + EXE_PREFIX + r"[A-Za-z]+\.exe$"

# Fun Time's mark, stamped into each copy so the row carrying its name carries
# its face too.  The project root, where every other window in this repo reads
# the same file from.
ICON_PATH = Path(__file__).resolve().parent.parent / "icon.ico"

_ROLE_RE = re.compile(r"^[A-Za-z]+$")

# Which source the copy was made from, recorded in the copy's own version
# resource.  Size and mtime alone cannot answer it any more -- rewriting the
# resource changes both -- and this says more than they did: it goes stale when
# the interpreter is upgraded AND when the label we would write here changes.
_STAMP_FIELD = "FunTimeSource"


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


def role_description(role: str) -> str:
    """What the Processes tab shows for *role* -- "Fun Time -- Audio Companion".

    The role is a single CamelCase word because it also has to be a file name;
    split back apart here so the row reads as English rather than as an
    identifier.
    """
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", role)
    return f"{APP_NAME} – {words}"


def is_fun_time_exe_name(name: str) -> bool:
    """Whether an image name is one of the role-named copies this module makes."""
    return name.lower().startswith(EXE_PREFIX.lower()) and name.lower().endswith(".exe")


# --- Building the resources -------------------------------------------------

def _wstr(text: str) -> bytes:
    return text.encode("utf-16-le") + b"\x00\x00"


def _pad4(data: bytes) -> bytes:
    return data + b"\x00" * (-len(data) % 4)


def _node(key: str, value: bytes, *, value_length: int, wtype: int) -> bytes:
    """One node of a version resource: header, key, then the aligned value.

    ``wLength`` counts the header, the key and the value together with the
    padding that aligns the value -- but not any padding that follows it, which
    belongs to the next sibling instead.
    """
    head = _pad4(struct.pack("<HHH", 0, value_length, wtype) + _wstr(key))
    body = head + value
    return struct.pack("<H", len(body)) + body[2:]


def build_version_info(fields: dict[str, str], *, lang: int = 0x0409, codepage: int = 0x04B0) -> bytes:
    """A whole VS_VERSIONINFO carrying *fields*.

    Built rather than patched: the strings we want are longer than the ones
    Python ships, and every length and offset in the format is relative, so
    editing one string in place means rewriting the structure around it anyway.
    """
    strings = b"".join(
        _pad4(_node(key, _wstr(value), value_length=len(value) + 1, wtype=1))
        for key, value in fields.items()
    )
    string_table = _pad4(_node(f"{lang:04X}{codepage:04X}", strings, value_length=0, wtype=1))
    string_file_info = _pad4(_node("StringFileInfo", string_table, value_length=0, wtype=1))
    translation = _node("Translation", struct.pack("<HH", lang, codepage), value_length=4, wtype=0)
    var_file_info = _pad4(_node("VarFileInfo", _pad4(translation), value_length=0, wtype=1))

    fixed = struct.pack(
        "<LLLLLLLLLLLLL",
        0xFEEF04BD,   # dwSignature
        0x00010000,   # dwStrucVersion
        0, 0,         # dwFileVersion MS / LS
        0, 0,         # dwProductVersion MS / LS
        0x3F,         # dwFileFlagsMask
        0,            # dwFileFlags
        0x00040004,   # dwFileOS = VOS_NT_WINDOWS32
        0x00000001,   # dwFileType = VFT_APP
        0,            # dwFileSubtype
        0, 0,         # dwFileDate MS / LS
    )
    return _node("VS_VERSION_INFO", fixed + string_file_info + var_file_info,
                 value_length=len(fixed), wtype=0)


def build_icon_resources(ico: bytes) -> tuple[list[bytes], bytes]:
    """Split a .ico into the images and the directory that indexes them.

    An .ico on disk and an icon in a PE are the same images behind two different
    directories: the file's points at byte offsets, the resource's at resource
    ids.  So the images go in untouched and only the directory is rebuilt.
    """
    reserved, kind, count = struct.unpack_from("<HHH", ico, 0)
    if reserved != 0 or kind != 1 or not count:
        raise ValueError("not an icon file")
    images: list[bytes] = []
    entries: list[bytes] = []
    for index in range(count):
        width, height, colors, pad, planes, bits, size, offset = struct.unpack_from(
            "<BBBBHHLL", ico, 6 + index * 16)
        images.append(ico[offset:offset + size])
        entries.append(struct.pack("<BBBBHHLH", width, height, colors, pad,
                                   planes, bits, size, index + 1))
    return images, struct.pack("<HHH", 0, 1, count) + b"".join(entries)


# --- Writing them into the copy ---------------------------------------------

_RT_ICON = 3
_RT_GROUP_ICON = 14
_RT_VERSION = 16
_LANG_EN_US = 0x0409


def _kernel32():
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.BeginUpdateResourceW.restype = wintypes.HANDLE
    dll.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    dll.UpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.WORD, wintypes.LPVOID, wintypes.DWORD]
    dll.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    return dll


def stamp_identity(exe: Path, *, description: str, role: str, source_stamp: str,
                   icon: bytes | None) -> None:
    """Write *description*, the source stamp and *icon* into *exe*'s resources.

    One update handle for all of it: ``EndUpdateResource`` rewrites the file
    once, so a failure part-way leaves the copy untouched rather than half
    relabelled.
    """
    version = build_version_info({
        "CompanyName": APP_NAME,
        "FileDescription": description,
        "InternalName": Path(exe).stem,
        "OriginalFilename": Path(exe).name,
        "ProductName": APP_NAME,
        _STAMP_FIELD: source_stamp,
    })
    dll = _kernel32()
    handle = dll.BeginUpdateResourceW(str(exe), False)
    if not handle:
        raise OSError(f"BeginUpdateResource failed ({ctypes.get_last_error()})")
    try:
        def put(kind: int, name: int, data: bytes) -> None:
            buf = ctypes.create_string_buffer(data, len(data))
            ok = dll.UpdateResourceW(handle, ctypes.cast(kind, wintypes.LPCWSTR),
                                     ctypes.cast(name, wintypes.LPCWSTR),
                                     _LANG_EN_US, buf, len(data))
            if not ok:
                raise OSError(f"UpdateResource failed ({ctypes.get_last_error()})")

        put(_RT_VERSION, 1, version)
        if icon is not None:
            images, directory = build_icon_resources(icon)
            for index, image in enumerate(images):
                put(_RT_ICON, index + 1, image)
            put(_RT_GROUP_ICON, 1, directory)
            # Python ships more icon images than Fun Time's mark has, and the
            # ones past ours stay in the file as orphans.  They are left there on
            # purpose: nothing references them once the group above is rewritten,
            # and sweeping them out is not free.  Deleting a resource id that is
            # not there returns success from UpdateResource and then fails the
            # whole EndUpdateResource with ERROR_INTERNAL_ERROR -- so a tidying
            # pass over a fixed id range threw away the relabelling it came with.
    except Exception:
        dll.EndUpdateResourceW(handle, True)  # discard
        raise
    if not dll.EndUpdateResourceW(handle, False):
        raise OSError(f"EndUpdateResource failed ({ctypes.get_last_error()})")


def read_version_field(exe: Path, field: str, *, lang: int = _LANG_EN_US,
                       codepage: int = 0x04B0) -> str | None:
    """Read one string out of *exe*'s version resource, or None if it has none."""
    version_dll = ctypes.WinDLL("version", use_last_error=True)
    version_dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, wintypes.LPDWORD]
    version_dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                                wintypes.DWORD, wintypes.LPVOID]
    version_dll.VerQueryValueW.argtypes = [wintypes.LPCVOID, wintypes.LPCWSTR,
                                           ctypes.POINTER(wintypes.LPVOID),
                                           ctypes.POINTER(wintypes.UINT)]
    size = version_dll.GetFileVersionInfoSizeW(str(exe), None)
    if not size:
        return None
    block = ctypes.create_string_buffer(size)
    if not version_dll.GetFileVersionInfoW(str(exe), 0, size, block):
        return None
    value = wintypes.LPVOID()
    length = wintypes.UINT()
    query = f"\\StringFileInfo\\{lang:04X}{codepage:04X}\\{field}"
    if not version_dll.VerQueryValueW(block, query, ctypes.byref(value), ctypes.byref(length)):
        return None
    if not length.value:
        return None
    return ctypes.wstring_at(value.value, length.value - 1)


# --- Making the copy --------------------------------------------------------

def _source_stamp(source: Path, description: str) -> str:
    """What the copy has to match to still be current.

    Both halves matter: the interpreter's size and mtime catch a Python upgrade
    replacing the launcher under us, and the description catches a change to
    what we would write today -- a copy relabelled in code but not on disk keeps
    the old row heading for good otherwise.
    """
    stat = source.stat()
    return f"{stat.st_size}:{int(stat.st_mtime)}:{description}"


def _is_current(copy: Path, stamp: str) -> bool:
    if not copy.is_file():
        return False
    try:
        return read_version_field(copy, _STAMP_FIELD) == stamp
    except OSError:
        return False


def identified_python_exe(python_exe: str | Path, role: str) -> str:
    """Return an interpreter that will run *role* under a name naming Fun Time.

    Makes the described, role-named copy beside *python_exe* if it is missing or
    stale, and returns it.  Returns *python_exe* unchanged if the copy cannot be
    made -- a read-only venv, an antivirus hold, a disk that is full.  The
    session then runs exactly as it did before this module existed, with
    anonymous children: a worse task list and a working app.
    """
    source = Path(python_exe)
    try:
        target = source.with_name(role_exe_name(source, role))
        description = role_description(role)
        stamp = _source_stamp(source, description)
    except (ValueError, OSError):
        logger.warning("Not naming the %s process", role, exc_info=True)
        return str(python_exe)

    if _is_current(target, stamp):
        return str(target)

    try:
        icon = ICON_PATH.read_bytes() if ICON_PATH.is_file() else None
    except OSError:
        icon = None

    try:
        # copyfile rather than copy2, which treats a directory destination as
        # "copy INTO this" -- so something of our name that is not a file (a
        # directory left by a botched cleanup) made the copy report success and
        # handed the launcher a directory to run.  copyfile refuses it instead.
        shutil.copyfile(source, target)
        stamp_identity(target, description=description, role=role,
                       source_stamp=stamp, icon=icon)
    except (OSError, ValueError):
        # A described copy already there, one label or one Python upgrade behind,
        # still names its process -- better than going back to an anonymous one.
        # That is the usual way to land here: last session's copy is still
        # running, so Windows refuses to overwrite the image and leaves it as it
        # was.  The test is what the file SAYS rather than that it exists,
        # because the other way to land here is a source that cannot carry a
        # description at all, and that leaves a copy which names nothing --
        # a file added to the venv for no benefit, and a launcher that would
        # report a process it cannot identify.  Undescribed, it goes.
        if _is_ours(target):
            logger.info("Kept the existing %s launcher; could not refresh it", target.name)
            return str(target)
        _discard(target)
        logger.warning("Not naming the %s process; launching unnamed", role, exc_info=True)
        return str(python_exe)
    return str(target)


def prepare_orchestrator_launcher() -> None:
    """Make the copy ``launch.vbs`` runs the orchestrator through next time.

    The orchestrator is the one process this module cannot name on the way in:
    naming it means writing a file with Python, and the process that would do
    the writing is the one being named.  So the launcher prefers the copy when
    it is there and falls back to plain ``python.exe`` when it is not, and the
    session makes it for the session after -- which costs one launch, once, and
    then heals itself for good.

    Derived from the interpreter's own directory rather than the project's, so a
    session running out of somewhere else names its launcher in the venv it is
    actually on.  The console interpreter by name, because that is the one the
    launcher runs: reading ``sys.executable`` would name a copy after a copy on
    every launch after the first.
    """
    identified_python_exe(Path(sys.executable).with_name("python.exe"), "Orchestrator")


def _is_ours(exe: Path) -> bool:
    """Whether *exe* is a copy this module stamped, whatever it says today.

    Asked by the private stamp field rather than by the label, so that renaming
    what we write does not make us stop recognizing -- and start deleting --
    copies we made ourselves.
    """
    if not exe.is_file():
        return False
    try:
        return read_version_field(exe, _STAMP_FIELD) is not None
    except OSError:
        return False


def _discard(target: Path) -> None:
    """Remove a copy that turned out to name nothing, if it is ours to remove."""
    try:
        if target.is_file():
            target.unlink()
    except OSError:
        logger.info("Left %s behind; it could not be removed", target.name, exc_info=True)
