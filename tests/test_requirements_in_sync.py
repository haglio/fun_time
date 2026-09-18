"""Verify requirements.txt stays in sync with pyproject.toml.

``pyproject.toml`` is the tested source of truth for dependencies (see
``test_dependency_completeness.py``); ``requirements.txt`` is a convenience
installer that must mirror it.  This guards the drift that previously let
``requirements.txt`` fall out of date — e.g. omitting PyQt6 and the voice deps so
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


def _pyproject_specs() -> set[str]:
    project = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]
    specs: set[str] = set(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        specs.update(group)
    return specs


def _requirements_specs() -> set[str]:
    lines = (raw.split("#", 1)[0].strip()
             for raw in REQUIREMENTS.read_text(encoding="utf-8").splitlines())
    return {line for line in lines if line}


def _names(specs: set[str]) -> set[str]:
    return {_canonical(_dist_name(spec)) for spec in specs}


def _tags(specs: set[str]) -> dict[str, str]:
    """Each git-pinned dependency against the ref it names."""
    return {_canonical(_dist_name(spec)): spec.rsplit("@", 1)[-1].strip()
            for spec in specs if "git+" in spec}


def test_requirements_matches_pyproject():
    pyproject = _names(_pyproject_specs())
    requirements = _names(_requirements_specs())

    missing = pyproject - requirements
    extra = requirements - pyproject
    assert not missing and not extra, (
        "requirements.txt is out of sync with pyproject.toml.\n"
        f"  declared in pyproject.toml but missing from requirements.txt: {sorted(missing)}\n"
        f"  in requirements.txt but not declared in pyproject.toml: {sorted(extra)}"
    )


def test_requirements_names_the_same_sibling_tags():
    """Names alone cannot see a stale pin, and all three drifted under that:
    ``requirements.txt`` named app_support v0.1.140, shared_ui v0.1.123 and
    player_core v0.1.264 while pyproject had moved on by thirteen, four and one
    tag, so ``pip install -r requirements.txt`` built against three versions of
    the family that nothing here was tested on."""
    assert _tags(_requirements_specs()) == _tags(_pyproject_specs())
