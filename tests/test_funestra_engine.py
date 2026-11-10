from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import fun_time.orchestrator as desktop
import fun_time_vr.orchestrator as headset
from fun_time.funestra_engine import engine_missing_abort


def _config(*sibling_checkouts: Path):
    return SimpleNamespace(paths=SimpleNamespace(
        python_exe=Path("fun_time/py.exe"),
        sibling_checkouts_path=";".join(str(d) for d in sibling_checkouts),
    ))


def _runner(returncodes, *, stdout=""):
    seq = iter(returncodes)
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, next(seq), stdout=stdout, stderr="")

    run.calls = calls
    return run


class _Funestras:
    def __init__(self, looks_in: Path, shared: Path, *, reason: str = "no engine"):
        self.looks_in, self.shared, self.reason = looks_in, shared, reason
        self.appdata_only_an_agents_shell_sees = shared.parent / "AppData"
        self.probed: list[str] = []
        self.asked_without_pythonpath: list[str] = []

    def __call__(self, cmd, **kwargs):
        code = cmd[2]
        asked_of_an_older_pinned_checkout = bool(kwargs.get("env", {}).get("PYTHONPATH"))
        if not asked_of_an_older_pinned_checkout:
            self.asked_without_pythonpath.append(code)
        if "_import_mpv" in code:
            self.probed.append(cmd[0])
            loaded = (self.looks_in / "libmpv-2.dll").is_file()
            return subprocess.CompletedProcess(
                cmd, 0 if loaded else 1, stdout="", stderr="" if loaded else self.reason)
        if "libmpv_dirs" in code:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{self.looks_in}\n", stderr="")
        if "machine_libmpv_dir" in code:
            answer = (self.appdata_only_an_agents_shell_sees
                      if asked_of_an_older_pinned_checkout else self.shared)
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{answer}\n", stderr="")
        raise AssertionError(f"unexpected command: {code}")


def _shared_engine(tmp_path: Path, contents: bytes = b"engine") -> Path:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "libmpv-2.dll").write_bytes(contents)
    return shared


def test_a_funestra_that_cannot_load_gets_the_shared_copy_where_it_looks_first(tmp_path: Path):
    the_vendor_folder_a_pinned_checkout_never_fetched = tmp_path / "funestra" / "vendor"
    funestras = _Funestras(the_vendor_folder_a_pinned_checkout_never_fetched,
                           _shared_engine(tmp_path))

    assert engine_missing_abort(_config(), run=funestras, alert=lambda _text: None) is False
    assert (the_vendor_folder_a_pinned_checkout_never_fetched
            / "libmpv-2.dll").read_bytes() == b"engine"


def test_where_the_shared_copy_is_comes_from_the_funestra_this_repo_pins(tmp_path: Path):
    pinned = tmp_path / "funestra"
    pinned.mkdir()
    funestras = _Funestras(pinned / "vendor", _shared_engine(tmp_path))

    assert engine_missing_abort(_config(pinned), run=funestras, alert=lambda _text: None) is False

    assert (pinned / "vendor" / "libmpv-2.dll").read_bytes() == b"engine"
    assert [code for code in funestras.asked_without_pythonpath
            if "machine_libmpv_dir" in code]
    assert not [code for code in funestras.asked_without_pythonpath
                if "_import_mpv" in code or "libmpv_dirs" in code]


def test_the_probe_imports_the_funestra_the_funestras_import_not_the_venvs(tmp_path: Path):
    pinned = tmp_path / "funestra"
    pinned.mkdir()
    run = _runner([0])

    assert engine_missing_abort(_config(pinned), run=run, alert=lambda _text: None) is False
    assert [kwargs["env"]["PYTHONPATH"].split(";")[0] for _cmd, kwargs in run.calls] == [
        str(pinned)]


def test_a_funestra_that_loads_is_spared_a_copy_of_the_large_engine(tmp_path: Path):
    looks_in = tmp_path / "vendor"
    looks_in.mkdir()
    (looks_in / "libmpv-2.dll").write_bytes(b"the one it already has")
    funestras = _Funestras(looks_in, _shared_engine(tmp_path, b"another copy"))

    assert engine_missing_abort(_config(), run=funestras, alert=lambda _text: None) is False
    assert (looks_in / "libmpv-2.dll").read_bytes() == b"the one it already has"
    assert funestras.probed == [str(Path("fun_time/py.exe"))]


def test_the_alert_says_where_the_funestra_looked_in_its_own_words(tmp_path: Path):
    said = ('Traceback (most recent call last):\n'
            '  File "<string>", line 1, in <module>\n'
            "OSError: The engine (libmpv) could not be loaded. Looked in: "
            "C:\\funestra\\vendor (no libmpv-2.dll in it)")
    funestras = _Funestras(tmp_path / "vendor", tmp_path / "no-shared-copy", reason=said)
    shown: list[str] = []

    assert engine_missing_abort(_config(), run=funestras, alert=shown.append) is True
    assert "Looked in: C:\\funestra\\vendor (no libmpv-2.dll in it)" in shown[0]
    assert shown[0].startswith("Fun Time can't start its Funestras")
    assert 'File "<string>"' not in shown[0]


def test_nothing_is_said_when_the_funestras_load_the_engine():
    shown: list[str] = []

    assert engine_missing_abort(_config(), run=_runner([0]), alert=shown.append) is False
    assert shown == []


def test_the_probe_is_the_engine_import_itself():
    run = _runner([0])

    engine_missing_abort(_config(), run=run, alert=lambda _text: None)

    cmd, _kwargs = run.calls[0]
    assert cmd[1] == "-c"
    assert "_import_mpv()" in cmd[2]


def test_a_funestra_whose_interpreter_cannot_say_where_it_looks_is_not_given_a_copy(tmp_path: Path):
    shared = _shared_engine(tmp_path)

    def run(cmd, **_kwargs):
        if "_import_mpv" in cmd[2]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no engine")
        if "machine_libmpv_dir" in cmd[2]:
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{shared.parent}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="cannot import funestra")

    assert engine_missing_abort(_config(), run=run, alert=lambda _text: None) is True
    assert list(tmp_path.rglob("libmpv-2.dll")) == [shared / "libmpv-2.dll"]


def test_both_the_desktop_and_headset_launches_gate_on_the_engine():
    for entry_point in (desktop, headset):
        source = Path(entry_point.__file__).read_text(encoding="utf-8")
        assert "engine_missing_abort(config" in source


def test_the_loading_screen_comes_down_before_the_alert_it_would_cover(tmp_path: Path):
    funestras = _Funestras(tmp_path / "vendor", tmp_path / "no-shared-copy")
    order: list[str] = []

    engine_missing_abort(_config(), run=funestras,
                         alert=lambda _text: order.append("alert"),
                         uncover=lambda: order.append("uncovered"))

    assert order == ["uncovered", "alert"]
