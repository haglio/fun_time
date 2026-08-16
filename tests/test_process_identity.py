from __future__ import annotations

import re
from pathlib import Path

import pytest

from fun_time.process_identity import (
    EXE_PREFIX,
    PROCESS_NAME_PATTERN,
    identified_python_exe,
    is_fun_time_exe_name,
    role_exe_name,
)


def _interpreter(tmp_path: Path, name: str = "pythonw.exe", body: bytes = b"MZ launcher") -> Path:
    exe = tmp_path / ".venv" / "Scripts" / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(body)
    return exe


class TestRoleExeName:
    def test_names_the_copy_for_its_role(self, tmp_path: Path):
        assert role_exe_name(_interpreter(tmp_path), "AudioCompanion") == "FunTime-AudioCompanion.exe"

    def test_keeps_the_interpreter_suffix_rather_than_assuming_one(self, tmp_path: Path):
        # The suffix decides nothing on its own, but taking it from the source
        # keeps a console interpreter and a windowed one from being told apart
        # by anything but which file was copied.
        assert role_exe_name(_interpreter(tmp_path, "python.exe"), "Nau").endswith(".exe")

    @pytest.mark.parametrize("role", ["Audio Companion", "audio-companion", "../evil", "", "Nau2"])
    def test_refuses_a_role_that_is_not_plain_letters(self, tmp_path: Path, role: str):
        # The role becomes a file name beside the interpreter, so anything with
        # a separator in it is a write somewhere nobody asked for.
        with pytest.raises(ValueError):
            role_exe_name(_interpreter(tmp_path), role)


class TestIdentifiedPythonExe:
    def test_copies_the_interpreter_to_a_role_named_sibling(self, tmp_path: Path):
        source = _interpreter(tmp_path)

        result = Path(identified_python_exe(source, "Portrait"))

        assert result == source.with_name("FunTime-Portrait.exe")
        assert result.read_bytes() == source.read_bytes()

    def test_copies_the_launcher_beside_it_so_the_venv_still_resolves(self, tmp_path: Path):
        # pyvenv.cfg is found one directory up from the interpreter, so a copy
        # anywhere but Scripts/ is a different (or no) virtualenv — with the
        # session's site-packages missing and every import of ours failing.
        source = _interpreter(tmp_path)

        result = Path(identified_python_exe(source, "Dashboard"))

        assert result.parent == source.parent

    def test_reuses_a_copy_that_is_already_current(self, tmp_path: Path):
        # Every launch asks, and the answer has to be "already there" — a copy
        # judged stale on its own timestamp would rewrite a running image on
        # every session and get refused for it.
        source = _interpreter(tmp_path)
        first = Path(identified_python_exe(source, "Genau"))
        stamp = first.stat().st_mtime_ns

        identified_python_exe(source, "Genau")

        assert first.stat().st_mtime_ns == stamp

    def test_refreshes_a_copy_left_over_from_an_older_interpreter(self, tmp_path: Path):
        # A Python upgrade replaces the launcher under us.  A copy that keeps
        # running the old one is a session on an interpreter nobody installed.
        source = _interpreter(tmp_path)
        stale = source.with_name("FunTime-Landscape.exe")
        stale.write_bytes(b"MZ an older launcher entirely")

        identified_python_exe(source, "Landscape")

        assert stale.read_bytes() == source.read_bytes()

    def test_falls_back_to_the_interpreter_when_the_copy_cannot_be_made(self, tmp_path: Path):
        # A read-only venv, an antivirus hold, a full disk.  Naming a process is
        # never worth failing a launch over: the session comes up anonymous.
        source = _interpreter(tmp_path)
        blocked = source.with_name("FunTime-Nau.exe")
        blocked.mkdir()  # a directory of that name: the copy cannot land

        assert identified_python_exe(source, "Nau") == str(source)

    def test_keeps_a_copy_it_could_not_refresh_rather_than_dropping_the_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        # The usual reason a refresh fails is that last session's copy is still
        # running and Windows will not overwrite a running image.  One Python
        # upgrade behind beats going back to an unidentifiable process.
        source = _interpreter(tmp_path)
        existing = source.with_name("FunTime-ClosingScreen.exe")
        existing.write_bytes(b"MZ an older launcher entirely")
        monkeypatch.setattr(
            "fun_time.process_identity.shutil.copyfile",
            lambda *a, **k: (_ for _ in ()).throw(PermissionError("in use")),
        )

        assert identified_python_exe(source, "ClosingScreen") == str(existing)

    def test_falls_back_rather_than_raising_on_a_role_it_cannot_name(self, tmp_path: Path):
        # A launch site is not a validation site: a bad role loses the name, not
        # the window.
        source = _interpreter(tmp_path)

        assert identified_python_exe(source, "Bad Role") == str(source)


class TestProcessNameMatching:
    @pytest.mark.parametrize(
        "name", ["FunTime-Portrait.exe", "FunTime-AudioCompanion.exe", "FunTime-Orchestrator.exe"],
    )
    def test_the_sweep_pattern_matches_the_copies_we_launch_under(self, name: str):
        # The satellite reap and the integration leftover sweep both bound
        # themselves by image name.  A role-named copy that the pattern misses
        # is a player no sweep can reach.
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
        # name check all have to agree on it or a session's processes divide
        # into ones that can be found and ones that cannot.
        assert EXE_PREFIX == "FunTime-"
        assert role_exe_name("pythonw.exe", "Nau").startswith(EXE_PREFIX)
        assert EXE_PREFIX in PROCESS_NAME_PATTERN
