from __future__ import annotations

import re
from pathlib import Path

from app_support.launcher import launchers
from app_support.process_identity_check import ProcessName, assert_the_process_is_named

from fun_time.process_identity import (
    LAUNCH_VBS_INTERPRETER,
    NAMER,
    prepare_orchestrator_launcher,
)
from fun_time.project_paths import PROJECT_DIR, PROJECT_ICON
from fun_time.windows_bridge_startup import reap_orphaned_satellites
from tests.made_up_machine import running

ROLES = (
    "Orchestrator", "Dashboard", "AudioCompanion", "LibraryBrowser",
    "MainFunestra", "Genau", "Origenerator", "Portrait", "Landscape",
    "ClosingScreen", "LoadingScreen",
)


class TestTheStringsOtherLanguagesMatchOn:
    def test_the_sweep_pattern_is_the_one_the_sweeps_were_written_against(self):
        assert NAMER.process_name_pattern == (
            r"^pythonw?\.exe$|^py\.exe$|^FunTime-[A-Za-z]+\.exe$")

    def test_the_satellite_reap_force_ends_only_images_the_pattern_names(self):
        stranded = "-m satellite --status-file C:/state/portrait_status.txt"
        ours = (51, "FunTime-Portrait.exe", stranded, 0.0)
        another_apps_satellite_module = (52, "Relay-Portrait.exe", stranded, 0.0)
        with running("fun_time.windows_bridge_startup",
                     [ours, another_apps_satellite_module]) as ended:
            reap_orphaned_satellites("satellite", ["C:/state/portrait_status.txt"])

        assert ended == [51]

    def test_the_launcher_would_silently_run_unnamed_without_the_namers_exact_name(self):
        (desktop,) = [spec for spec in launchers(PROJECT_DIR) if spec.file == "launch.vbs"]
        named = NAMER.exe_name(desktop.interpreter, "Orchestrator")

        assert named == "FunTime-Orchestrator.exe"
        assert desktop.named_interpreter == named


class TestTheRolesThisRepoLaunches:
    def test_the_sweep_pattern_reaches_every_one_of_them(self):
        for role in ROLES:
            name = NAMER.exe_name("pythonw.exe", role)
            assert re.match(NAMER.process_name_pattern, name), name

    def test_the_sweep_pattern_still_matches_a_plain_interpreter(self):
        for name in ("pythonw.exe", "python.exe", "py.exe"):
            assert re.match(NAMER.process_name_pattern, name), name

    def test_the_force_ending_sweeps_pattern_matches_none_of_his_own_apps(self):
        for name in ("notepad.exe", "mypythonw.exe", "FunTimeOther.exe", "python3.exe"):
            assert not re.match(NAMER.process_name_pattern, name), name

    def test_the_broker_names_its_own_processes_and_the_namer_claims_none(self):
        for name in ("Broker-Tray.exe", "Broker-Broker.exe"):
            assert not re.match(NAMER.process_name_pattern, name), name
            assert not NAMER.owns_exe_name(name), name

    def test_a_role_one_word_for_its_file_name_is_english_in_its_row(self):
        assert NAMER.exe_name("pythonw.exe", "AudioCompanion") == "FunTime-AudioCompanion.exe"
        assert NAMER.description("AudioCompanion") == "Fun Time \u2013 Audio Companion"
        assert NAMER.description("LibraryBrowser") == "Fun Time \u2013 Library Browser"
        for role in ROLES:
            assert NAMER.description(role).startswith("Fun Time"), role

    def test_the_reap_claims_their_copies_and_no_bare_interpreter(self):
        for role in ROLES:
            assert NAMER.owns_exe_name(NAMER.exe_name("pythonw.exe", role)), role
        for name in ("python.exe", "pythonw.exe", "py.exe"):
            assert not NAMER.owns_exe_name(name), name


class TestTheOneCopyFunTimeMakesAheadOfASession:
    def test_it_is_made_from_the_launchers_interpreter_never_from_a_named_copy(self):
        (desktop,) = [spec for spec in launchers(PROJECT_DIR) if spec.file == "launch.vbs"]

        assert desktop.interpreter == LAUNCH_VBS_INTERPRETER
        assert not NAMER.owns_exe_name(LAUNCH_VBS_INTERPRETER)

    def test_app_supports_check_of_its_machinery_is_handed_fun_times_answers(self, tmp_path: Path):
        assert_the_process_is_named(
            prepare_orchestrator_launcher, tmp_path,
            ProcessName(
                app_name="Fun Time",
                role="Orchestrator",
                interpreter=LAUNCH_VBS_INTERPRETER,
                row="Fun Time \u2013 Orchestrator",
                icon=PROJECT_ICON,
            ),
        )


def test_the_copies_carry_fun_time_s_own_mark():
    assert NAMER.icon == PROJECT_ICON
