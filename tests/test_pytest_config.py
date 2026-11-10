from __future__ import annotations

import ast
import configparser
import inspect
import tomllib
from pathlib import Path

import pytest
from _pytest.config import findpaths

pytestmark = pytest.mark.repo_check

REPO_ROOT = Path(__file__).resolve().parent.parent

PYTESTS_SECTION_IN = {"tox.ini": "pytest", "setup.cfg": "tool:pytest"}


def _settings() -> dict:
    with (REPO_ROOT / "pyproject.toml").open("rb") as config:
        return tomllib.load(config)["tool"]["pytest"]["ini_options"]


def test_the_settings_live_in_pyproject():
    assert _settings()


def _config_files_in_the_order_this_pytest_looks_for_them() -> list[str]:
    looked_for = [
        node.value for node in ast.walk(ast.parse(inspect.getsource(findpaths.locate_config)))
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "config_names"]
    assert len(looked_for) == 1, "pytest no longer lists its config files where this reads them"
    as_pytest_itself_computes_it = compile(ast.Expression(looked_for[0]), findpaths.__file__, "eval")
    return list(eval(as_pytest_itself_computes_it, vars(findpaths)))


def _files_pytest_would_read_instead_of_pyproject(root: Path) -> list[str]:
    order = _config_files_in_the_order_this_pytest_looks_for_them()
    return [name for name in order[:order.index("pyproject.toml")] if (root / name).exists()]


@pytest.mark.parametrize("outranking", ["pytest.toml", ".pytest.toml", "pytest.ini", ".pytest.ini"])
def test_a_file_pytest_reads_before_pyproject_is_found(outranking, tmp_path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / outranking).write_text("", encoding="utf-8")

    assert _files_pytest_would_read_instead_of_pyproject(tmp_path) == [outranking]


def test_nothing_outranks_pyproject_as_the_config_file():
    assert _files_pytest_would_read_instead_of_pyproject(REPO_ROOT) == []

    for name, section in PYTESTS_SECTION_IN.items():
        candidate = REPO_ROOT / name
        if not candidate.exists():
            continue
        parsed = configparser.ConfigParser()
        parsed.read(candidate, encoding="utf-8")
        assert not parsed.has_section(section), f"{name} shadows pyproject.toml"


def test_no_test_stops_every_patch_in_the_process_to_end_its_own():
    stop_every_patch = "stop" + "all("

    assert not [
        test_file.name for test_file in (REPO_ROOT / "tests").rglob("*.py")
        if stop_every_patch in test_file.read_text(encoding="utf-8")]


def test_an_ordinary_run_does_not_collect_the_integration_suite():
    assert "integration" in _settings()["norecursedirs"]


def test_the_sanitize_plugin_and_the_hang_clock_are_on_every_run():
    addopts = _settings()["addopts"]

    assert "app_support.sanitize.pytest_plugin" in addopts
    assert "--timeout=" in addopts
