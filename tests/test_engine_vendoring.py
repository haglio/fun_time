"""Self-heal: put the engine DLL next to each player's player_core so it resolves
by package-relative path -- the one lookup that survives a real launch when the
machine-wide copy cannot be found from the launched process."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from fun_time.engine_vendoring import ensure_engine_vendored, ensure_vendored


def _locator(vendor_dll: Path, machine_dll: Path, *, rc: int = 0):
    def run(_cmd, **_kwargs):
        return subprocess.CompletedProcess(
            _cmd, rc, stdout=f"{vendor_dll}\n{machine_dll}\n", stderr=""
        )

    return run


def test_copies_the_engine_into_vendor_when_it_is_missing(tmp_path: Path):
    machine = tmp_path / "machine" / "libmpv-2.dll"
    machine.parent.mkdir(parents=True)
    machine.write_bytes(b"engine")
    vendor = tmp_path / "vendor" / "libmpv-2.dll"

    ensure_vendored("py.exe", None, run=_locator(vendor, machine))

    assert vendor.read_bytes() == b"engine"


def test_leaves_an_already_vendored_engine_untouched(tmp_path: Path):
    machine = tmp_path / "machine" / "libmpv-2.dll"
    machine.parent.mkdir(parents=True)
    machine.write_bytes(b"new")
    vendor = tmp_path / "vendor" / "libmpv-2.dll"
    vendor.parent.mkdir(parents=True)
    vendor.write_bytes(b"already here")

    ensure_vendored("py.exe", None, run=_locator(vendor, machine))

    assert vendor.read_bytes() == b"already here"


def test_does_nothing_when_the_machine_copy_is_also_absent(tmp_path: Path):
    machine = tmp_path / "machine" / "libmpv-2.dll"  # never created
    vendor = tmp_path / "vendor" / "libmpv-2.dll"

    ensure_vendored("py.exe", None, run=_locator(vendor, machine))

    assert not vendor.exists()


def test_does_nothing_when_the_interpreter_cannot_locate_player_core(tmp_path: Path):
    vendor = tmp_path / "vendor" / "libmpv-2.dll"
    machine = tmp_path / "machine" / "libmpv-2.dll"
    machine.parent.mkdir(parents=True)
    machine.write_bytes(b"engine")

    ensure_vendored("py.exe", None, run=_locator(vendor, machine, rc=1))

    assert not vendor.exists()


def test_a_missing_interpreter_is_skipped():
    calls = []

    def run(_cmd, **_kwargs):
        calls.append(_cmd)
        raise AssertionError("should not spawn for a null interpreter")

    ensure_vendored(None, None, run=run)

    assert calls == []


def test_ensure_engine_vendored_covers_both_players_interpreters(tmp_path: Path):
    machine = tmp_path / "machine" / "libmpv-2.dll"
    machine.parent.mkdir(parents=True)
    machine.write_bytes(b"engine")
    seen: list[str] = []

    def run(cmd, **_kwargs):
        seen.append(cmd[0])
        vendor = tmp_path / cmd[0] / "libmpv-2.dll"
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{vendor}\n{machine}\n", stderr="")

    config = SimpleNamespace(
        paths=SimpleNamespace(
            genau_python_exe="genau",
            python_exe="funtime",
            genau_project_dirs=(),
        )
    )

    ensure_engine_vendored(config, run=run)

    assert seen == ["genau", "funtime"]


def test_both_the_desktop_and_headset_launches_self_heal_the_engine():
    # The headset's players load the engine from the same venvs as the desktop's,
    # so both entry points must vendor it before launching -- the pairing rule.
    import fun_time.orchestrator as desktop
    import fun_time_vr.orchestrator as headset

    for entry_point in (desktop, headset):
        source = Path(entry_point.__file__).read_text(encoding="utf-8")
        assert "ensure_engine_vendored(config)" in source
