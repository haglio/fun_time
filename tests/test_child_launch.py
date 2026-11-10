from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from fun_time.child_launch import no_child_log, no_console_window, open_child_log

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = (ROOT / "fun_time", ROOT / "satellite", ROOT / "fun_time_vr", ROOT / "main_funestra")

_SUPPLIES_THE_PAIR = "no_child_log"

_KNOWN_BY_NAME_TO_SET_CREATE_NO_WINDOW = frozenset({
    "no_console_window",
    "hidden_subprocess_kwargs",
    "origenerator_launch_kwargs",
})

_SPAWNS = frozenset({"Popen", "run", "check_output"})


def _spawn_calls() -> list[tuple[Path, ast.Call]]:
    found = []
    for path in sorted(p for pkg in PACKAGES for p in pkg.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found.extend((path, node) for node in ast.walk(tree)
                     if isinstance(node, ast.Call) and _is_a_spawn(node.func))
    return found


def _is_a_spawn(func: ast.expr) -> bool:
    return (isinstance(func, ast.Attribute) and func.attr in _SPAWNS
            and isinstance(func.value, ast.Name) and func.value.id == "subprocess")


def _popen_calls() -> list[tuple[Path, ast.Call]]:
    return [(path, call) for path, call in _spawn_calls() if call.func.attr == "Popen"]


def _unpacks(call: ast.Call, names: frozenset[str] | set[str]) -> bool:
    return any(
        keyword.arg is None
        and isinstance(keyword.value, ast.Call)
        and isinstance(keyword.value.func, ast.Name)
        and keyword.value.func.id in names
        for keyword in call.keywords
    )


def _says_where_the_output_goes(call: ast.Call) -> bool:
    named = {keyword.arg for keyword in call.keywords}
    if "stdout" in named and "stderr" in named:
        return True
    return _unpacks(call, {_SUPPLIES_THE_PAIR})


def _asks_for_no_console(call: ast.Call) -> bool:
    if any(keyword.arg == "creationflags" for keyword in call.keywords):
        return True
    if _unpacks(call, _KNOWN_BY_NAME_TO_SET_CREATE_NO_WINDOW):
        return True
    return _unpacks_kwargs_a_swept_caller_built(call)


def _unpacks_kwargs_a_swept_caller_built(call: ast.Call) -> bool:
    return any(keyword.arg is None and isinstance(keyword.value, ast.Name)
               for keyword in call.keywords)


@pytest.mark.repo_check
def test_there_are_spawns_to_check():
    assert len(_popen_calls()) >= 15
    assert len(_spawn_calls()) >= 20


@pytest.mark.repo_check
@pytest.mark.parametrize(
    ("path", "call"),
    [pytest.param(path, call, id=f"{path.name}:{call.lineno}")
     for path, call in _popen_calls()],
)
def test_every_child_says_where_its_output_goes(path: Path, call: ast.Call):
    assert _says_where_the_output_goes(call), (
        f"{path.relative_to(ROOT)}:{call.lineno}: Popen with no stdout/stderr. "
        "An unset pair is INHERITED, and what this process inherited is the "
        "launcher's redirect on state/launcher.log -- a child that outlives the "
        "session then blocks the next launch outright. Pass **no_child_log(), "
        "or a log of the child's own."
    )


@pytest.mark.repo_check
@pytest.mark.parametrize(
    ("path", "call"),
    [pytest.param(path, call, id=f"{path.name}:{call.lineno}")
     for path, call in _spawn_calls()],
)
def test_every_child_asks_for_no_console_window(path: Path, call: ast.Call):
    assert _asks_for_no_console(call), (
        f"{path.relative_to(ROOT)}:{call.lineno}: spawn with no creationflags. "
        "A session the crossing relay started runs under the windowed "
        "interpreter and has no console, so Windows gives a console child one "
        "of its own -- a window on the user's screen. Pass "
        "**hidden_subprocess_kwargs() for a console tool, or "
        "**no_console_window() for a child that opens windows of its own."
    )


def test_the_helper_sends_the_pair_to_devnull():
    assert no_child_log() == {
        "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}


def test_no_console_window_leaves_the_childs_own_first_window_showing():
    assert no_console_window() == {"creationflags": subprocess.CREATE_NO_WINDOW}


class TestOpenChildLog:
    def test_banner_names_the_launch_and_its_argv(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"

        with open_child_log(log_file, ["pythonw.exe", "-m", "satellite", "--title", "Portrait"]):
            pass

        flashed = log_file.read_text(encoding="utf-8")
        assert "pythonw.exe -m satellite --title Portrait" in flashed

    def test_appends_so_a_prior_session_survives_the_next_launch(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"
        log_file.write_text("Traceback from the session that died\n", encoding="utf-8")

        with open_child_log(log_file, ["pythonw.exe"]):
            pass

        assert "Traceback from the session that died" in log_file.read_text(encoding="utf-8")

    def test_rolls_the_log_aside_once_it_passes_the_cap(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"
        log_file.write_text("x" * 200, encoding="utf-8")

        with open_child_log(log_file, ["pythonw.exe"], max_bytes=100):
            pass

        assert (tmp_path / "portrait_satellite.log.1").read_text(encoding="utf-8") == "x" * 200
        assert "x" * 200 not in log_file.read_text(encoding="utf-8")

    def test_a_log_that_cannot_be_opened_hands_back_a_handle_that_discards_instead_of_raising(self, tmp_path: Path):
        blocker = tmp_path / "state"
        blocker.write_text("a file where the directory should be", encoding="utf-8")

        with open_child_log(blocker / "portrait_satellite.log", ["pythonw.exe"]) as handle:
            handle.write(b"lost")

        assert blocker.read_text(encoding="utf-8") == "a file where the directory should be"

    def test_a_log_that_cannot_be_rolled_aside_is_appended_to_all_the_same(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"
        log_file.write_text("x" * 200, encoding="utf-8")
        (tmp_path / "portrait_satellite.log.1").mkdir()

        with open_child_log(log_file, ["pythonw.exe"], max_bytes=100):
            pass

        written = log_file.read_text(encoding="utf-8")
        assert written.startswith("x" * 200)
        assert "launch: pythonw.exe" in written

    def test_creates_the_state_directory(self, tmp_path: Path):
        log_file = tmp_path / "state" / "portrait_satellite.log"

        with open_child_log(log_file, ["pythonw.exe"]):
            pass

        assert log_file.exists()

    def test_a_log_under_the_cap_is_left_where_it_is(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"
        log_file.write_text("x" * 100, encoding="utf-8")

        with open_child_log(log_file, ["pythonw.exe"], max_bytes=100):
            pass

        assert not (tmp_path / "portrait_satellite.log.1").exists()

    def test_only_one_rolled_generation_is_kept(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"
        (tmp_path / "portrait_satellite.log.1").write_text("older", encoding="utf-8")
        log_file.write_text("y" * 200, encoding="utf-8")

        with open_child_log(log_file, ["pythonw.exe"], max_bytes=100):
            pass

        assert (tmp_path / "portrait_satellite.log.1").read_text(encoding="utf-8") == "y" * 200

    def test_a_refused_roll_still_launches_by_appending_to_the_big_file(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"
        log_file.write_text("z" * 200, encoding="utf-8")
        blocker = tmp_path / "portrait_satellite.log.1"
        blocker.mkdir()
        (blocker / "occupied").write_text("", encoding="utf-8")

        with open_child_log(log_file, ["pythonw.exe"], max_bytes=100):
            pass

        assert log_file.read_text(encoding="utf-8").startswith("z" * 200)
        assert "launch: pythonw.exe" in log_file.read_text(encoding="utf-8")

    def test_a_log_that_cannot_be_opened_hands_back_a_handle_that_takes_writes(self, tmp_path: Path):
        log_file = tmp_path / "portrait_satellite.log"
        log_file.mkdir()

        with open_child_log(log_file, ["pythonw.exe"]) as handle:
            assert handle.write(b"traceback") == len(b"traceback")
