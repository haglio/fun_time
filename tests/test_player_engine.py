"""The players' engine, checked and repaired at launch: every player must be able
to import libmpv before a session opens, or the launch says why instead of
leaving him an empty room under the loading screen."""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import fun_time.orchestrator as desktop
import fun_time_vr.orchestrator as headset
from fun_time.player_engine import engine_missing_abort, player_interpreters


def _config(*project_dirs: Path, genau_python="genau/py.exe", python="fun_time/py.exe"):
    return SimpleNamespace(paths=SimpleNamespace(
        python_exe=Path(python),
        genau_python_exe=Path(genau_python) if genau_python else None,
        genau_project_path=";".join(str(d) for d in project_dirs),
    ))


def _runner(returncodes, *, stdout=""):
    seq = iter(returncodes)
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, next(seq), stdout=stdout, stderr="")

    run.calls = calls
    return run


class _Players:
    """A fake ``subprocess.run`` answering the three things a launch asks a player's
    interpreter: whether the engine imports, where that player looks for it first,
    and where the shared copy is.  The import succeeds once the DLL is in the place
    it looks, which is what the repair puts there."""

    def __init__(self, looks_in: Path, shared: Path, *, reason: str = "no engine"):
        self.looks_in, self.shared, self.reason = looks_in, shared, reason
        self.probed: list[str] = []
        self.asked_without_pythonpath: list[str] = []

    def __call__(self, cmd, **kwargs):
        code = cmd[2]
        if not kwargs.get("env", {}).get("PYTHONPATH"):
            self.asked_without_pythonpath.append(code)
        if "_import_mpv" in code:
            self.probed.append(cmd[0])
            loaded = (self.looks_in / "libmpv-2.dll").is_file()
            return subprocess.CompletedProcess(
                cmd, 0 if loaded else 1, stdout="", stderr="" if loaded else self.reason)
        if "libmpv_dirs" in code:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{self.looks_in}\n", stderr="")
        if "machine_libmpv_dir" in code:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{self.shared}\n", stderr="")
        raise AssertionError(f"unexpected command: {code}")


def _shared_engine(tmp_path: Path, contents: bytes = b"engine") -> Path:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "libmpv-2.dll").write_bytes(contents)
    return shared


def test_a_player_that_cannot_load_gets_the_shared_copy_where_it_looks_first(tmp_path: Path):
    """The engine is fetched rather than tracked, so a player_core checkout a session
    pins has no ``vendor/`` of its own and the player dies on import.  The shared
    copy goes where that checkout looks first and the player is asked again, instead
    of the launch refusing over a gap it can close."""
    looks_in = tmp_path / "player_core" / "vendor"
    players = _Players(looks_in, _shared_engine(tmp_path))

    assert engine_missing_abort(_config(), run=players, alert=lambda _text: None) is False
    assert (looks_in / "libmpv-2.dll").read_bytes() == b"engine"


def test_where_the_shared_copy_is_comes_from_the_player_core_this_repo_pins(tmp_path: Path):
    """The session's own pin is left off that one question.  A player_core checkout
    from before 2026-09-26 answers it with a folder under AppData, which is private
    to the app an agent's shell runs inside and empty for everything the user
    launches -- so asking the pinned checkout would name a source that is not there."""
    pinned = tmp_path / "player_core"
    pinned.mkdir()
    players = _Players(pinned / "vendor", _shared_engine(tmp_path))

    engine_missing_abort(_config(pinned), run=players, alert=lambda _text: None)

    assert [code for code in players.asked_without_pythonpath
            if "machine_libmpv_dir" in code]
    assert not [code for code in players.asked_without_pythonpath
                if "_import_mpv" in code or "libmpv_dirs" in code]


def test_each_player_is_probed_with_the_pythonpath_it_is_launched_with(tmp_path: Path):
    """A session pins its player_core by putting a checkout of it first on every
    player's PYTHONPATH, so a probe without that tests the venv's player_core and
    not the one the player imports.  It passed while every player died looking for
    the engine, and the room came up under a loading screen that never lifted
    (2026-09-22)."""
    pinned = tmp_path / "player_core"
    pinned.mkdir()
    run = _runner([0, 0])

    assert engine_missing_abort(_config(pinned), run=run, alert=lambda _text: None) is False
    assert [kwargs["env"]["PYTHONPATH"].split(";")[0] for _cmd, kwargs in run.calls] == [
        str(pinned), str(pinned)]


def test_a_player_that_loads_the_engine_is_left_alone(tmp_path: Path):
    """No second copy and not even the question: the DLL is 117 MB, and a launch that
    handed one to every pinned checkout would spend that at every start."""
    looks_in = tmp_path / "vendor"
    looks_in.mkdir()
    (looks_in / "libmpv-2.dll").write_bytes(b"the one it already has")
    players = _Players(looks_in, _shared_engine(tmp_path, b"another copy"))

    assert engine_missing_abort(_config(), run=players, alert=lambda _text: None) is False
    assert (looks_in / "libmpv-2.dll").read_bytes() == b"the one it already has"
    assert players.probed == [str(Path("genau/py.exe")), str(Path("fun_time/py.exe"))]


def test_the_alert_carries_what_the_player_itself_said(tmp_path: Path):
    """His previews sat under a loading screen that never lifted while each player's
    log held the whole answer -- which folders were looked in and what was not in
    them.  On screen that text beats any sentence written here, and the frames of the
    traceback under it are not part of it."""
    said = ('Traceback (most recent call last):\n'
            '  File "<string>", line 1, in <module>\n'
            "OSError: The engine (libmpv) could not be loaded. Looked in: "
            "C:\\player_core\\vendor (no libmpv-2.dll in it)")
    players = _Players(tmp_path / "vendor", tmp_path / "no-shared-copy", reason=said)
    shown: list[str] = []

    assert engine_missing_abort(_config(), run=players, alert=shown.append) is True
    assert "Looked in: C:\\player_core\\vendor (no libmpv-2.dll in it)" in shown[0]
    assert "the main player" in shown[0]
    assert 'File "<string>"' not in shown[0]


def test_nothing_is_said_when_every_player_loads_the_engine():
    shown: list[str] = []

    assert engine_missing_abort(_config(), run=_runner([0, 0]), alert=shown.append) is False
    assert shown == []


def test_the_first_player_that_cannot_be_fixed_stops_the_check(tmp_path: Path):
    players = _Players(tmp_path / "vendor", tmp_path / "no-shared-copy")

    assert engine_missing_abort(_config(), run=players, alert=lambda _text: None) is True
    assert str(Path("fun_time/py.exe")) not in players.probed


def test_the_probe_is_the_engine_import_itself():
    # A probe that stopped importing mpv would pass forever over a dead engine.
    run = _runner([0, 0])

    engine_missing_abort(_config(), run=run, alert=lambda _text: None)

    cmd, _kwargs = run.calls[0]
    assert cmd[1] == "-c"
    assert "_import_mpv()" in cmd[2]


def test_a_player_whose_interpreter_cannot_say_where_it_looks_is_not_given_a_copy(tmp_path: Path):
    """Every answer here comes from running the player's own interpreter, which can
    fail for its own reasons; a copy is made only where one was actually asked for."""
    shared = _shared_engine(tmp_path)

    def run(cmd, **_kwargs):
        if "_import_mpv" in cmd[2]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no engine")
        if "machine_libmpv_dir" in cmd[2]:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{shared.parent}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="cannot import player_core")

    assert engine_missing_abort(_config(), run=run, alert=lambda _text: None) is True
    assert list(tmp_path.rglob("libmpv-2.dll")) == [shared / "libmpv-2.dll"]


def test_player_interpreters_maps_each_player_to_its_own_venv_python():
    paths = SimpleNamespace(
        python_exe=Path("fun_time/py.exe"),
        genau_python_exe=Path("genau/py.exe"),
    )

    assert player_interpreters(paths) == {
        "the main player": Path("genau/py.exe"),
        "the side players": Path("fun_time/py.exe"),
    }


def test_player_interpreters_omits_the_main_player_without_a_genau_python():
    paths = SimpleNamespace(python_exe=Path("fun_time/py.exe"), genau_python_exe=None)

    assert player_interpreters(paths) == {"the side players": Path("fun_time/py.exe")}


def test_both_the_desktop_and_headset_launches_gate_on_the_engine():
    # A dead engine takes the headset's players down exactly as it does the
    # desktop's, so both entry points must refuse rather than open onto empty
    # windows -- the desktop/headset pairing the engineering law requires.

    for entry_point in (desktop, headset):
        source = Path(entry_point.__file__).read_text(encoding="utf-8")
        assert "engine_missing_abort(config" in source
