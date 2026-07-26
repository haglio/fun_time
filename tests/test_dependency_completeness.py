"""Verify every third-party import in the packages is declared in pyproject.toml."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Every top-level package ships from this repo, so all must be covered: the
# satellite player pulls in numpy and Pillow that the orchestrator alone would
# not have justified, and fun_time_vr brings the OpenXR/GL stack.
PACKAGE_DIRS = (
    PROJECT_ROOT / "fun_time",
    PROJECT_ROOT / "satellite",
    PROJECT_ROOT / "fun_time_vr",
)
PYPROJECT = PROJECT_ROOT / "pyproject.toml"

# Map import name -> pyproject.toml dependency name when they differ.
IMPORT_TO_DIST: dict[str, str] = {
    "serial": "pyserial",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "pygame": "pygame-ce",
    "PyQt6": "PyQt6",
    "xr": "pyopenxr",
    "OpenGL": "PyOpenGL",
    "glfw": "glfw",
}

# Standard library modules that should never be flagged.
_STDLIB_MODULES: set[str] | None = None


def _stdlib_modules() -> set[str]:
    global _STDLIB_MODULES
    if _STDLIB_MODULES is None:
        _STDLIB_MODULES = set(sys.stdlib_module_names)
    return _STDLIB_MODULES


def _parse_pyproject_deps() -> set[str]:
    """Return the set of dependency names from pyproject.toml (lowercased)."""
    text = PYPROJECT.read_text(encoding="utf-8")
    in_deps = False
    deps: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("dependencies"):
            in_deps = True
            continue
        if in_deps:
            if stripped == "]":
                break
            # Lines look like: "pyserial",  or  "numpy",
            name = stripped.strip('",').strip()
            if name:
                deps.add(name.lower())
    return deps


class _TryAwareVisitor(ast.NodeVisitor):
    """Collect import nodes, tagging whether they sit inside a try block."""

    def __init__(self) -> None:
        self.imports: list[tuple[str, bool]] = []  # (top_level_name, inside_try)
        self._in_try = False

    def visit_Try(self, node: ast.Try) -> None:
        old = self._in_try
        self._in_try = True
        for child in node.body + node.handlers + node.orelse + node.finalbody:
            self.visit(child)
        self._in_try = old

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append((alias.name.split(".")[0], self._in_try))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module and node.level == 0:
            self.imports.append((node.module.split(".")[0], self._in_try))


def _collect_third_party_imports() -> dict[str, set[str]]:
    """Scan all .py files in the package and return {import_name: {files}}.

    Only collects *unconditional* imports — those inside try/except are
    considered optional and excluded.
    """
    third_party: dict[str, set[str]] = {}
    stdlib = _stdlib_modules()
    # The shared siblings are installed editable from local paths, so they are
    # deliberately absent from [project.dependencies] — see pyproject.toml.
    local_packages = {
        "fun_time", "satellite",           # this repo
        "app_support", "player_core", "shared_ui",  # sibling repos
    }

    for package_dir in PACKAGE_DIRS:
        for py_file in package_dir.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            except SyntaxError:
                continue

            visitor = _TryAwareVisitor()
            visitor.visit(tree)

            for top, inside_try in visitor.imports:
                if top not in stdlib and top not in local_packages and not inside_try:
                    third_party.setdefault(top, set()).add(
                        str(py_file.relative_to(PROJECT_ROOT))
                    )

    return third_party


def test_all_third_party_imports_declared_in_pyproject():
    declared = _parse_pyproject_deps()
    imports = _collect_third_party_imports()

    missing: list[str] = []
    for import_name, files in sorted(imports.items()):
        dist_name = IMPORT_TO_DIST.get(import_name, import_name)
        if dist_name.lower() not in declared:
            missing.append(f"  {import_name} (pip: {dist_name}) imported by: {', '.join(sorted(files))}")

    assert not missing, (
        "Third-party imports not declared in pyproject.toml [project.dependencies]:\n"
        + "\n".join(missing)
    )
