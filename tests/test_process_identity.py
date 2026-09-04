"""What Fun Time itself decides about how its processes are named.

The machinery -- the version resource, the icon group, the copy and its
staleness check -- belongs to :mod:`app_support.process_identity` and is tested
there.  What is left here is the part only this repo can be wrong about: the two
strings its launcher and its process sweep are written against, the roles it
actually launches, and the one copy it makes a session in advance.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import patch

from fun_time.process_identity import NAMER, prepare_orchestrator_launcher
from fun_time.project_paths import PROJECT_DIR, PROJECT_ICON
from fun_time.windows_bridge_startup import reap_orphaned_satellites

# Every role this repo launches a child under, gathered from the launch sites in
# windows_bridge_startup, windows_bridge_orchestrator and library_browser, plus
# the one the launcher runs.  A role reaches the task list and a file name, so
# the set is worth naming somewhere a reader can see all of it at once.
ROLES = (
    "Orchestrator", "Dashboard", "AudioCompanion", "LibraryBrowser",
    "Nau", "Genau", "Origenerator", "Portrait", "Landscape",
    "ClosingScreen", "LoadingScreen",
)


class TestTheStringsOtherLanguagesMatchOn:
    """Two strings leave Python: an image name a VBScript looks for on disk, and
    a regex three PowerShell sweeps interpolate.  Neither has a caller that
    would fail if it changed, so both are held here to the byte."""

    def test_the_sweep_pattern_is_the_one_the_sweeps_were_written_against(self):
        # Spelled out rather than derived, because a derivation would agree with
        # itself after any change.  app_support's namer escapes the prefix --
        # re.escape turns the hyphen into ``\-`` -- and this is the exact string
        # the running sweeps match today.
        assert NAMER.process_name_pattern == (
            r"^pythonw?\.exe$|^py\.exe$|^FunTime-[A-Za-z]+\.exe$")

    def test_the_satellite_reap_is_the_sweep_that_carries_it(self):
        # Pinning the string proves nothing on its own: a sweep that stopped
        # interpolating it, or interpolated a bare "pythonw", would reach past
        # this repo's own players and force-kill whatever else the machine runs
        # under a Python.
        with patch("fun_time.windows_bridge_startup.subprocess.run") as run, patch(
            "fun_time.windows_bridge_startup.subprocess_window_kwargs", return_value={}
        ):
            reap_orphaned_satellites("satellite", ["C:/state/portrait_status.txt"])

        assert NAMER.process_name_pattern in run.call_args.args[0][-1]

    def test_the_launcher_looks_for_the_name_the_namer_gives_the_orchestrator(self):
        # launch.vbs greps the venv for this file by name and runs it if it is
        # there.  A namer that starts producing anything else does not fail --
        # the launcher quietly falls back to the plain interpreter and the
        # orchestrator goes back to being an anonymous row, for good.
        named = NAMER.exe_name("python.exe", "Orchestrator")

        assert named == "FunTime-Orchestrator.exe"
        assert named in (PROJECT_DIR / "launch.vbs").read_text(encoding="utf-8")


class TestTheRolesThisRepoLaunches:
    def test_the_sweep_pattern_reaches_every_one_of_them(self):
        # The satellite reap and the integration leftover sweep both bound
        # themselves by image name.  A role-named copy the pattern misses is a
        # player no sweep can reach.
        for role in ROLES:
            name = NAMER.exe_name("pythonw.exe", role)
            assert re.match(NAMER.process_name_pattern, name), name

    def test_the_sweep_pattern_still_matches_a_plain_interpreter(self):
        # The copy is best-effort, so a child can still arrive under the plain
        # interpreter and must stay reachable.
        for name in ("pythonw.exe", "python.exe", "py.exe"):
            assert re.match(NAMER.process_name_pattern, name), name

    def test_the_sweep_pattern_leaves_everything_else_alone(self):
        # These sweeps force-kill what they match, so a pattern that reaches one
        # of the user's own apps is the worst failure this module can have.
        for name in ("notepad.exe", "mypythonw.exe", "FunTimeOther.exe",
                     "python3.exe", "Broker-Tray.exe"):
            assert not re.match(NAMER.process_name_pattern, name), name

    def test_each_one_reads_as_english_under_the_app_s_own_name(self):
        # What the Processes tab actually shows.  The role has to be one word to
        # be a file name; the row is where it comes back apart.
        assert NAMER.description("AudioCompanion") == "Fun Time – Audio Companion"
        assert NAMER.description("LibraryBrowser") == "Fun Time – Library Browser"
        for role in ROLES:
            assert NAMER.description(role).startswith("Fun Time"), role

    def test_the_reap_claims_their_copies_and_no_bare_interpreter(self):
        # The integration reap is bounded by the hidden desktop and by the image
        # name, never by a command line -- and the run's own pytest is on that
        # desktop under a bare python.
        for role in ROLES:
            assert NAMER.owns_exe_name(NAMER.exe_name("pythonw.exe", role)), role
        for name in ("python.exe", "pythonw.exe", "py.exe"):
            assert not NAMER.owns_exe_name(name), name


class TestPrepareOrchestratorLauncher:
    def test_names_a_copy_of_the_console_interpreter_beside_the_running_one(self):
        # The console one by name: launch.vbs runs python.exe, and reading
        # sys.executable would name a copy after a copy on every launch after
        # the first -- FunTime-Orchestrator.exe copied to itself, once per run.
        with patch.object(NAMER, "named_exe") as named_exe:
            prepare_orchestrator_launcher()

        source, role = named_exe.call_args[0]
        assert Path(source) == Path(sys.executable).with_name("python.exe")
        assert role == "Orchestrator"


def test_the_copies_carry_fun_time_s_own_mark():
    """The row naming the app shows the app's face, and it is the icon every
    other window in this repo draws from."""
    assert NAMER.icon == PROJECT_ICON
