"""Verify requirements.txt stays in sync with pyproject.toml.

``pyproject.toml`` is the tested source of truth for dependencies (see
``test_dependency_completeness.py``); ``requirements.txt`` is a convenience
installer that must mirror it.  This guards the drift that previously let
``requirements.txt`` fall behind — e.g. omitting PyQt6 and the voice deps so
``pip install -r requirements.txt`` could not bring up the dashboard or voice.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"

# Characters that begin a version specifier, extra, or environment marker.
_NAME_TERMINATORS = (";", "[", "=", "<", ">", "~", "!", " ")


def _dist_name(spec: str) -> str:
    """Reduce a requirement spec to its bare distribution name."""
    for terminator in _NAME_TERMINATORS:
        spec = spec.split(terminator, 1)[0]
    return spec.strip()


def _canonical(name: str) -> str:
    """PEP 503-style normalization for comparing distribution names."""
    return name.strip().lower().replace("_", "-")


def _pyproject_dependencies() -> set[str]:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    specs: set[str] = set(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        specs.update(group)
    return {_canonical(_dist_name(spec)) for spec in specs}


def _requirements_dependencies() -> set[str]:
    names: set[str] = set()
    for raw_line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line:
            names.add(_canonical(_dist_name(line)))
    return names


def test_requirements_matches_pyproject():
    pyproject = _pyproject_dependencies()
    requirements = _requirements_dependencies()

    missing = pyproject - requirements
    extra = requirements - pyproject
    assert not missing and not extra, (
        "requirements.txt is out of sync with pyproject.toml.\n"
        f"  declared in pyproject.toml but missing from requirements.txt: {sorted(missing)}\n"
        f"  in requirements.txt but not declared in pyproject.toml: {sorted(extra)}"
    )
