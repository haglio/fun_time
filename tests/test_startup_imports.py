"""Guard the launch's import phase against a break that only shows at runtime.

Several startup modules do real work at *import* time: ``voice_commands`` builds
its phrase table from ``load_content()["clip_jump_phrases"]`` and ``filter_vocab``
from ``load_content()["acts"]``, both at module level.  A missing overlay
key therefore raises ``KeyError`` the instant orchestrator startup imports the
module — before any window opens — and nothing else in the unit suite imports
that graph in a fresh interpreter, so a bad overlay (or any broken top-level
import on the launch path) would first surface as a black-screen launch.

The import is driven in a subprocess, faithful to ``python -m fun_time.orchestrator``
(the entrypoint ``launch.vbs`` starts), against the committed
``content.example.json`` — the only content overlay a fresh or public checkout
has.  So the committed example alone must be enough to bring the graph up.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
EXAMPLE_CONTENT = PROJECT_DIR / "content.example.json"

# The launch entrypoint.  Importing it transitively pulls the whole launch
# graph — the single-instance guard, the manifest, the dispatch loop, the
# players' wiring, and voice_control -> voice_commands -> filter_vocab ->
# content, which is where the module-level overlay reads live.
_STARTUP_MODULES = (
    "fun_time.orchestrator",     # python -m fun_time.orchestrator
    # The session machinery, which the launch loads a second later, under the
    # cover: the overlay reads above are on its side of that import.
    "fun_time.windows_bridge_orchestrator",
    "fun_time.voice_commands",   # module-level load_content()["clip_jump_phrases"]
)

# What may not be loaded to raise the cover, all of it seconds of import on a
# cold machine: the session machinery and the libraries under it.  Each used to
# load before anything was on screen, which is what he was watching a bare
# desktop for.
_NOT_BEFORE_THE_COVER = (
    "fun_time.windows_bridge_orchestrator",
    "numpy",
    "cv2",
    "PyQt6.QtWidgets",
    "tkinter",
)

# Overlay keys read at import time by the launch graph; each must be present in
# the committed example or the graph refuses to import.
_IMPORT_TIME_OVERLAY_KEYS = ("clip_jump_phrases", "acts")


def _import_startup_graph(content_overlay: Path) -> subprocess.CompletedProcess:
    """Import the launch module graph in a fresh interpreter, with every content
    overlay read forced onto *content_overlay* — simulating a checkout whose sole
    content overlay is that file, whatever the developer's git-ignored
    ``content.local.json`` happens to hold.

    The overlay path is patched onto ``content.load_content`` before the consumer
    modules import, so their module-level reads resolve to it; the entrypoint and
    the two lazily-imported launch modules are then imported the way startup does.
    """
    imports = "; ".join(f"import {name}" for name in _STARTUP_MODULES)
    driver = (
        "import sys; from pathlib import Path; overlay = Path(sys.argv[1]); "
        "import fun_time.content as c; _orig = c.load_content; "
        "c.load_content = lambda *a, **k: _orig(overlay, overlay); "
        + imports
    )
    return subprocess.run(
        [sys.executable, "-c", driver, str(content_overlay)],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
    )


def test_startup_graph_imports_against_the_example_overlay():
    """A public checkout, whose only content overlay is the committed example,
    must be able to import the whole launch graph without error."""
    result = _import_startup_graph(EXAMPLE_CONTENT)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("required_key", _IMPORT_TIME_OVERLAY_KEYS)
def test_a_missing_required_overlay_key_fails_the_import(tmp_path, required_key):
    """The guard above is only meaningful if a missing overlay key actually
    fails the import — otherwise it could pass vacuously.  Drop each import-time
    key from the example and confirm the graph refuses to come up, naming it."""
    data = json.loads(EXAMPLE_CONTENT.read_text(encoding="utf-8"))
    del data[required_key]
    broken_overlay = tmp_path / "content.json"
    broken_overlay.write_text(json.dumps(data), encoding="utf-8")

    result = _import_startup_graph(broken_overlay)
    assert result.returncode != 0
    assert required_key in result.stderr


def test_the_launch_loads_nothing_the_cover_does_not_need():
    """The first thing a launch does is cover the monitors, so what it imports
    to get there is what he waits through.  The rest loads under the cover."""
    result = subprocess.run(
        [sys.executable, "-c",
         "import fun_time.orchestrator, sys;"
         f"print([m for m in {_NOT_BEFORE_THE_COVER!r} if m in sys.modules])"],
        cwd=PROJECT_DIR, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", (
        f"the launch loads {result.stdout.strip()} before it can raise the cover"
    )


def test_the_sys_path_override_still_sits_between_the_two_import_blocks():
    """The one module-level side effect on the launch path, pinned in place.

    ``apply_genau_dirs_to_sys_path()`` must run AFTER ``checkout_overrides`` is
    imported (it is what provides it) and BEFORE the bridge is, because this
    process resolves ``player_core`` through the venv — the primary's — and a
    branch leaning on an unlanded player_core change then imports code the
    primary does not have.  That is a session that dies at launch, and it
    reached him that way on 2026-08-13.  An import tidied above the call, or the
    call slid below one, puts it back; this says so instead of a comment hoping
    someone reads it.
    """
    tree = ast.parse((PROJECT_DIR / "fun_time" / "orchestrator.py").read_text(encoding="utf-8"))
    provides = applies = uses = None
    # The bridge import is inside the call that runs the session, not at the top
    # of the module, so the walk reaches into the functions to find it.
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "checkout_overrides":
            provides = node.lineno
        elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", "") == "apply_genau_dirs_to_sys_path"):
            applies = node.lineno
        elif (isinstance(node, ast.ImportFrom)
                and node.module == "windows_bridge_orchestrator"):
            uses = node.lineno

    assert provides and applies and uses, "the launch path's import shape has moved"
    assert provides < applies < uses, (
        "the genau/player_core override has to be applied after "
        "checkout_overrides is imported and before the bridge is"
    )
