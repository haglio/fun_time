from __future__ import annotations

import re
import shutil
import struct
import sys
from pathlib import Path

import pytest

from fun_time.process_identity import (
    _STAMP_FIELD,
    APP_NAME,
    EXE_PREFIX,
    PROCESS_NAME_PATTERN,
    build_icon_resources,
    identified_python_exe,
    is_fun_time_exe_name,
    read_version_field,
    role_description,
    role_exe_name,
)

# The copy is stamped with UpdateResource, which only works on a real PE, so
# these exercise an actual interpreter rather than a stub file.  It is the same
# interpreter the suite is running under, copied somewhere disposable.
REAL_INTERPRETER = Path(sys.executable)


def _interpreter(tmp_path: Path, name: str = "pythonw.exe") -> Path:
    exe = tmp_path / ".venv" / "Scripts" / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REAL_INTERPRETER, exe)
    return exe


def _stub_interpreter(tmp_path: Path, name: str = "pythonw.exe") -> Path:
    """A stand-in interpreter for the decision tests: the fake below never
    inspects it, so a few bytes in the right place beat copying a real one."""
    exe = tmp_path / ".venv" / "Scripts" / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ stub interpreter")
    return exe


class _FakeResources:
    """The two Win32 resource calls as a recording fake.

    What ``stamp_identity`` wrote is what ``read_version_field`` answers, which
    is the contract the real pair keeps -- so the keep/refresh/discard decisions
    can be pinned on any platform, and the real-interpreter tests above are left
    to prove the real pair honours it.
    """

    def __init__(self) -> None:
        self.stamped: dict[Path, dict[str, str]] = {}

    def stamp(self, exe: Path, *, description: str, source_stamp: str,
              icon: bytes | None) -> None:
        self.stamped[Path(exe)] = {
            "FileDescription": description, _STAMP_FIELD: source_stamp}

    def read_field(self, exe: Path, field: str) -> str | None:
        return (self.stamped.get(Path(exe)) or {}).get(field)


def _use_fake_resources(monkeypatch: pytest.MonkeyPatch, resources: _FakeResources) -> None:
    monkeypatch.setattr("fun_time.process_identity.stamp_identity", resources.stamp)
    monkeypatch.setattr("fun_time.process_identity.read_version_field", resources.read_field)


class TestRoleExeName:
    def test_names_the_copy_for_its_role(self):
        assert role_exe_name("pythonw.exe", "AudioCompanion") == "FunTime-AudioCompanion.exe"

    def test_keeps_the_interpreter_suffix_rather_than_assuming_one(self):
        # Taking the suffix from the source keeps a console interpreter and a
        # windowed one from being told apart by anything but which was copied.
        assert role_exe_name("python.exe", "Nau").endswith(".exe")

    @pytest.mark.parametrize("role", ["Audio Companion", "audio-companion", "../evil", "", "Nau2"])
    def test_refuses_a_role_that_is_not_plain_letters(self, role: str):
        # The role becomes a file name beside the interpreter, so anything with
        # a separator in it is a write somewhere nobody asked for.
        with pytest.raises(ValueError):
            role_exe_name("pythonw.exe", role)


class TestRoleDescription:
    def test_leads_with_the_app_so_every_row_says_fun_time(self):
        # This string, not the file name, is what the Processes tab displays --
        # the first attempt renamed only the file and the list still read
        # "Python" all the way down.
        assert role_description("Nau").startswith(APP_NAME)

    def test_splits_the_role_back_into_words(self):
        # The role is one CamelCase word because it doubles as a file name; the
        # row is read by a person.
        assert role_description("AudioCompanion") == f"{APP_NAME} – Audio Companion"
        assert role_description("LibraryBrowser") == f"{APP_NAME} – Library Browser"


class TestBuildIconResources:
    def test_reindexes_the_directory_onto_resource_ids(self):
        """An .ico indexes its images by byte offset and a PE indexes them by
        resource id, so only the directory is rebuilt — the images go in as they
        came out, and each directory entry names the id its image was given."""
        images, directory = build_icon_resources(_two_frame_ico())

        assert len(images) == 2
        reserved, kind, count = struct.unpack_from("<HHH", directory, 0)
        assert (reserved, kind, count) == (0, 1, 2)
        ids = [struct.unpack_from("<H", directory, 6 + i * 14 + 12)[0] for i in range(2)]
        assert ids == [1, 2]

    def test_refuses_something_that_is_not_an_icon(self):
        with pytest.raises(ValueError):
            build_icon_resources(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)


def _two_frame_ico() -> bytes:
    """A minimal two-image .ico — enough to index, not a real picture."""
    first, second = b"\x01" * 40, b"\x02" * 60
    header = struct.pack("<HHH", 0, 1, 2)
    offset = len(header) + 32
    entries = (struct.pack("<BBBBHHLL", 16, 16, 0, 0, 1, 32, len(first), offset)
               + struct.pack("<BBBBHHLL", 32, 32, 0, 0, 1, 32, len(second), offset + len(first)))
    return header + entries + first + second


class TestIdentifiedPythonExe:
    def test_copies_the_interpreter_to_a_role_named_sibling(self, tmp_path: Path):
        source = _interpreter(tmp_path)

        result = Path(identified_python_exe(source, "Portrait"))

        assert result == source.with_name("FunTime-Portrait.exe")
        assert result.is_file()

    def test_the_copy_describes_itself_as_fun_time(self, tmp_path: Path):
        """The whole point: the Processes tab reads the file description, so a
        copy that is merely renamed still shows up as one more "Python"."""
        source = _interpreter(tmp_path)

        result = Path(identified_python_exe(source, "AudioCompanion"))

        assert read_version_field(result, "FileDescription") == role_description("AudioCompanion")
        assert read_version_field(result, "ProductName") == APP_NAME

    def test_copies_the_launcher_beside_it_so_the_venv_still_resolves(self, tmp_path: Path):
        # pyvenv.cfg is found one directory up from the interpreter, so a copy
        # anywhere but Scripts/ is a different (or no) virtualenv — with the
        # session's site-packages missing and every import of ours failing.
        source = _interpreter(tmp_path)

        result = Path(identified_python_exe(source, "Dashboard"))

        assert result.parent == source.parent

    def test_keeps_the_interpreters_own_application_manifest(self, tmp_path: Path):
        """Rewriting resources must not cost the manifest that comes with them:
        it carries the DPI awareness every Qt window in this session inherits,
        so losing it would rescale the whole app to make a task list readable."""
        source = _interpreter(tmp_path)

        result = Path(identified_python_exe(source, "Genau"))

        assert _has_manifest(result), "the copy lost its application manifest"

    def test_reuses_a_copy_that_is_already_current(self, tmp_path: Path):
        # Asked on every launch, and the answer has to be "already there" — a
        # copy judged stale on its own would rewrite a running image every
        # session and be refused for it.
        source = _interpreter(tmp_path)
        first = Path(identified_python_exe(source, "Genau"))
        stamp = first.stat().st_mtime_ns

        identified_python_exe(source, "Genau")

        assert first.stat().st_mtime_ns == stamp

    def test_refreshes_a_copy_made_from_an_older_interpreter(self, tmp_path: Path):
        # A Python upgrade replaces the launcher under us.  A copy that keeps
        # running the old one is a session on an interpreter nobody installed.
        source = _interpreter(tmp_path)
        stale = Path(identified_python_exe(source, "Landscape"))
        stale.write_bytes(b"MZ not even a launcher")

        identified_python_exe(source, "Landscape")

        assert stale.stat().st_size > 1000
        assert read_version_field(stale, "FileDescription") == role_description("Landscape")

    def test_refreshes_a_copy_whose_label_this_version_no_longer_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """The stamp records the label as well as the interpreter, because a
        copy relabelled in code but not on disk would keep the old row heading
        for good — the file is still current by every other measure."""
        source = _interpreter(tmp_path)
        identified_python_exe(source, "Dashboard")
        monkeypatch.setattr("fun_time.process_identity.APP_NAME", "Renamed App")

        result = Path(identified_python_exe(source, "Dashboard"))

        assert read_version_field(result, "FileDescription") == "Renamed App – Dashboard"

    def test_falls_back_to_the_interpreter_when_the_copy_cannot_be_made(self, tmp_path: Path):
        # A read-only venv, an antivirus hold, a full disk.  Naming a process is
        # never worth failing a launch over: the session comes up anonymous.
        source = _interpreter(tmp_path)
        source.with_name("FunTime-Nau.exe").mkdir()  # the copy cannot land there

        assert identified_python_exe(source, "Nau") == str(source)

    def test_falls_back_when_the_interpreter_cannot_carry_a_description(self, tmp_path: Path):
        """Stamping needs a real executable.  Anything else — a shim, a shell
        wrapper — loses only its name, and a launch that would have worked
        still works."""
        source = tmp_path / ".venv" / "Scripts" / "pythonw.exe"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"#!/bin/sh\nexec python \"$@\"\n")

        assert identified_python_exe(source, "Nau") == str(source)

    def test_keeps_a_copy_it_could_not_refresh_rather_than_dropping_the_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        # The usual reason a refresh fails is that last session's copy is still
        # running and Windows will not overwrite a running image.  One Python
        # upgrade behind beats going back to an unidentifiable process.
        #
        # On the recording fake rather than on a real PE, so the decision is
        # pinned where no Win32 exists: with the real pair this reads the copy
        # back as undescribed off Windows, takes the discard path instead, and
        # passes anyway -- proving nothing on the platform the suite runs on.
        source = _stub_interpreter(tmp_path)
        _use_fake_resources(monkeypatch, _FakeResources())
        existing = Path(identified_python_exe(source, "ClosingScreen"))
        monkeypatch.setattr("fun_time.process_identity.APP_NAME", "Renamed App")
        monkeypatch.setattr(
            "fun_time.process_identity.shutil.copyfile",
            lambda *a, **k: (_ for _ in ()).throw(PermissionError("in use")),
        )

        assert identified_python_exe(source, "ClosingScreen") == str(existing)
        assert existing.is_file(), "the copy still in use was deleted"

    def test_falls_back_rather_than_raising_on_a_role_it_cannot_name(self, tmp_path: Path):
        # A launch site is not a validation site: a bad role loses the name, not
        # the window.
        source = _interpreter(tmp_path)

        assert identified_python_exe(source, "Bad Role") == str(source)


def _has_manifest(exe: Path) -> bool:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.LoadLibraryExW.restype = wintypes.HMODULE
    k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
    k32.FindResourceW.restype = wintypes.HANDLE
    k32.FindResourceW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p]
    k32.FreeLibrary.argtypes = [wintypes.HMODULE]
    module = k32.LoadLibraryExW(str(exe), None, 0x00000002)  # LOAD_LIBRARY_AS_DATAFILE
    if not module:
        return False
    try:
        return bool(k32.FindResourceW(module, ctypes.c_void_p(1), ctypes.c_void_p(24)))
    finally:
        k32.FreeLibrary(module)


class TestProcessNameMatching:
    @pytest.mark.parametrize(
        "name", ["FunTime-Portrait.exe", "FunTime-AudioCompanion.exe", "FunTime-Orchestrator.exe"],
    )
    def test_the_sweep_pattern_matches_the_copies_we_launch_under(self, name: str):
        # The satellite reap and the integration leftover sweep both bound
        # themselves by image name.  A role-named copy the pattern misses is a
        # player no sweep can reach.
        assert re.match(PROCESS_NAME_PATTERN, name)

    @pytest.mark.parametrize("name", ["pythonw.exe", "python.exe", "py.exe"])
    def test_the_sweep_pattern_still_matches_a_plain_interpreter(self, name: str):
        # The copy is best-effort, so a child can still arrive under the plain
        # interpreter and must stay reachable.
        assert re.match(PROCESS_NAME_PATTERN, name)

    @pytest.mark.parametrize(
        "name", ["notepad.exe", "mypythonw.exe", "FunTimeOther.exe", "python3.exe"],
    )
    def test_the_sweep_pattern_leaves_everything_else_alone(self, name: str):
        # These sweeps force-kill what they match, so a pattern that reaches one
        # of the user's own apps is the worst failure this module can have.
        assert not re.match(PROCESS_NAME_PATTERN, name)

    def test_recognizes_our_copies_by_name(self):
        assert is_fun_time_exe_name("FunTime-Dashboard.exe")
        assert is_fun_time_exe_name("funtime-dashboard.exe")

    def test_does_not_claim_a_name_that_merely_starts_the_same(self):
        assert not is_fun_time_exe_name("FunTimeSetup.msi")
        assert not is_fun_time_exe_name("pythonw.exe")

    def test_the_prefix_is_what_both_answers_are_built_from(self):
        # One spelling: the launcher's VBScript copy, the sweep pattern and the
        # name check all have to agree on it, or a session's processes divide
        # into ones that can be found and ones that cannot.
        assert EXE_PREFIX == "FunTime-"
        assert role_exe_name("pythonw.exe", "Nau").startswith(EXE_PREFIX)
        assert EXE_PREFIX in PROCESS_NAME_PATTERN
