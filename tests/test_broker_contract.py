"""The broker sweep has to reach a broker running under its own name.

None of the names that takes is this repo's to choose: the broker publishes
them beside its launcher and this session reads them.  Copied here instead,
renaming that package left the sweep matching nothing and the source date
unreadable, with nothing red anywhere.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app_support.process_identity import ProcessNamer

from fun_time import broker_contract
from fun_time.windows_bridge_startup import (
    broker_process_started_at,
    broker_source_mtime,
    stop_broker_processes,
)
from tests.made_up_machine import running

# A broker checkout as it publishes itself -- invented, not this machine's, so
# the test says what it depends on rather than inheriting it.
PUBLISHED = {
    "app_name": "Relay",
    "package_dir": "relay_pkg",
    "broker_module": "relay_pkg.app",
    "tray_module": "relay_pkg.tray",
    "tray_launcher": "launch_relay_tray.vbs",
}


def _a_broker_checkout(tmp_path: Path, **published: str) -> Path:
    """A checkout publishing PUBLISHED, and the launcher path this session holds."""
    named = {**PUBLISHED, **published}
    (tmp_path / broker_contract.CONTRACT_FILE).write_text(
        json.dumps(named), encoding="utf-8")
    launcher = tmp_path / named["tray_launcher"]
    launcher.write_text("' a launcher", encoding="utf-8")
    return launcher


MACHINE = [
    (21, "Relay-Broker.exe", r"C:\relay\Relay-Broker.exe -m relay_pkg.app --config r.json", 2000.0),
    (22, "Relay-Tray.exe", r"C:\relay\Relay-Tray.exe -m relay_pkg.tray --config r.json", 900.0),
    (23, "wscript.exe", r'wscript.exe "C:\relay\launch_relay_tray.vbs"', 800.0),
    (24, "pythonw.exe", r"pythonw.exe -m relay_pkg.app --config r.json", 1500.0),
    (25, "pythonw.exe", r"pythonw.exe -m wire.app", 1200.0),
    (31, "FunTime-MainPlayer.exe", r"FunTime-MainPlayer.exe -m main_player --config c.json", 100.0),
    (32, "pythonw.exe", r"pythonw.exe -m satellite --title Portrait AI Player", 100.0),
    (33, "wscript.exe", r'wscript.exe "C:\Fun Time\launch.vbs"', 100.0),
]


def _swept(launcher: Path | None) -> list[int]:
    with running("fun_time.windows_bridge_startup", MACHINE) as ended:
        stop_broker_processes(launcher)
    return sorted(ended)


def _probed(launcher: Path | None) -> float | None:
    with running("fun_time.windows_bridge_startup", MACHINE):
        return broker_process_started_at(launcher)


class TestTheImagePattern:
    """Built from the published label by the same rule the broker names by."""

    def test_it_matches_the_names_a_broker_of_that_name_launches_under(self, tmp_path):
        pattern = broker_contract.read(_a_broker_checkout(tmp_path)).image_pattern

        for role in ("Broker", "Tray"):
            name = ProcessNamer(PUBLISHED["app_name"]).exe_name("pythonw.exe", role)
            assert re.match(pattern, name), name

    def test_it_still_matches_a_broker_that_could_not_be_named(self, tmp_path):
        # The copy is best-effort, so a broker can arrive under the plain
        # interpreter and must stay reachable.
        pattern = broker_contract.read(_a_broker_checkout(tmp_path)).image_pattern

        for name in ("pythonw.exe", "python.exe", "py.exe"):
            assert re.match(pattern, name), name

    def test_it_leaves_this_sessions_own_processes_alone(self, tmp_path):
        """The sweep force-kills what it matches, and this session's own
        children are named too.  The command-line half bounds it as well, but
        the image half must not be the thing that saves us."""
        pattern = broker_contract.read(_a_broker_checkout(tmp_path)).image_pattern

        for name in ("FunTime-MainPlayer.exe", "FunTime-Dashboard.exe", "notepad.exe"):
            assert not re.match(pattern, name), name


class TestWhatTheSweepEnds:
    def test_the_broker_its_tray_and_their_launcher_under_any_image_they_run_as(self, tmp_path):
        """The tray belongs in the sweep, or it survives the kill and restarts the
        broker just stopped."""
        assert _swept(_a_broker_checkout(tmp_path)) == [21, 22, 23, 24]

    def test_a_renamed_package_moves_the_sweep_with_it(self, tmp_path):
        """The whole point: a rename there is followed here."""
        launcher = _a_broker_checkout(
            tmp_path, package_dir="wire", broker_module="wire.app",
            tray_module="wire.tray")

        assert _swept(launcher) == [23, 25]

    def test_a_broker_that_publishes_nothing_is_left_alone(self, tmp_path):
        """A sweep with no names is one that force-kills by guesswork."""
        (tmp_path / "launch_relay_tray.vbs").write_text("' a launcher", encoding="utf-8")

        assert _swept(tmp_path / "launch_relay_tray.vbs") == []


class TestWhatTheStartupProbeLooksFor:
    """``ensure_broker`` asks when the running broker started, to restart one
    older than its own code.  Answered None for a named broker, the restart
    could never fire and a stale broker went on dropping every verb newer than
    itself (bug 10)."""

    def test_it_dates_the_oldest_broker_and_never_its_tray(self, tmp_path):
        assert _probed(_a_broker_checkout(tmp_path)) == 1500.0

    def test_it_asks_nothing_of_a_broker_that_publishes_nothing(self, tmp_path):
        (tmp_path / "launch_relay_tray.vbs").write_text("' a launcher", encoding="utf-8")

        assert _probed(tmp_path / "launch_relay_tray.vbs") is None

    def test_no_broker_running_cannot_be_dated(self, tmp_path):
        launcher = _a_broker_checkout(
            tmp_path, package_dir="gone", broker_module="gone.app", tray_module="gone.tray")

        assert _probed(launcher) is None


class TestWhichSourcesDateTheBroker:
    def test_it_walks_the_package_the_broker_published(self, tmp_path):
        launcher = _a_broker_checkout(tmp_path)
        package = tmp_path / PUBLISHED["package_dir"]
        package.mkdir()
        (package / "app.py").write_text("", encoding="utf-8")
        os.utime(package / "app.py", (3000.0, 3000.0))
        # Beside it, and newer: config and logs change without changing the code.
        (tmp_path / "relay_config.json").write_text("{}", encoding="utf-8")
        os.utime(tmp_path / "relay_config.json", (9000.0, 9000.0))

        assert broker_source_mtime(launcher) == 3000.0

    def test_a_file_that_is_not_source_does_not_date_it(self, tmp_path):
        """What the process loaded is the package's .py files, so a log written
        among them must not read as a code change -- that would restart the
        broker on every startup."""
        launcher = _a_broker_checkout(tmp_path)
        package = tmp_path / PUBLISHED["package_dir"]
        package.mkdir()
        (package / "app.py").write_text("", encoding="utf-8")
        os.utime(package / "app.py", (3000.0, 3000.0))
        (package / "relay.log").write_text("", encoding="utf-8")
        os.utime(package / "relay.log", (9000.0, 9000.0))

        assert broker_source_mtime(launcher) == 3000.0

    def test_a_broker_that_publishes_nothing_cannot_be_dated(self, tmp_path):
        (tmp_path / "launch_relay_tray.vbs").write_text("' a launcher", encoding="utf-8")

        assert broker_source_mtime(tmp_path / "launch_relay_tray.vbs") is None

    def test_no_launcher_at_all_cannot_be_dated(self):
        assert broker_source_mtime(None) is None
