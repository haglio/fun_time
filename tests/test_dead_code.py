"""This repo's dead-code gate, and the three gates that are its own.

The dead-code checks are `app_support.dead_code` and `app_support.unread`, the
family's one shape; the packages are scanned one at a time, since scanned
together they hide each other's corpses. Below them, what only this repo asks:
that no module reaches into another's privates, that prose does not outgrow the
code it explains, and that nothing new binds Win32 under the layer that exists
to."""
from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

from app_support import unread
from app_support.dead_code import (
    assert_every_package_is_scanned,
    assert_no_dead_code,
    assert_no_function_takes_an_argument_it_never_reads,
    assert_nothing_is_imported_or_assigned_and_left_unread,
    assert_whitelist_is_live,
)

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = (ROOT / "fun_time", ROOT / "main_player", ROOT / "satellite", ROOT / "fun_time_vr")
SCANNED = PACKAGES
WHITELIST = ROOT / "vulture_whitelist.py"


def _package_sources():
    return sorted(p for pkg in PACKAGES for p in pkg.rglob("*.py"))


def test_no_dead_code():
    assert_no_dead_code(*SCANNED, whitelist=WHITELIST, each_alone=True)


def test_the_whitelist_still_suppresses_what_it_claims_to():
    assert_whitelist_is_live(*SCANNED, whitelist=WHITELIST, each_alone=True)


def test_every_package_in_the_tree_is_scanned():
    assert_every_package_is_scanned(ROOT, ("fun_time", "main_player", "satellite", "fun_time_vr"))


def test_nothing_is_imported_or_assigned_and_left_unread():
    assert_nothing_is_imported_or_assigned_and_left_unread(ROOT, *SCANNED, ROOT / "tools", ROOT / "tests")


def test_no_function_takes_an_argument_it_never_reads():
    # ARG001 alone, as before: a framework override (Qt's paintEvent, the satellite
    # player's pump) is handed arguments it is free to ignore.
    assert_no_function_takes_an_argument_it_never_reads(ROOT, *SCANNED, ROOT / "tools")


def test_no_module_level_constant_goes_unread():
    unread.assert_no_module_constant_goes_unread(ROOT, SCANNED)


def test_no_constructor_parameter_is_stored_and_never_read():
    unread.assert_no_constructor_parameter_is_stored_and_never_read(ROOT, SCANNED)


def test_no_dataclass_field_goes_unread():
    # CommandFiles.player_file asks for a side's file by kind, so the two the
    # satellites' channels now take that way are read by getattr rather than by
    # name. Their four neighbours escape this only because other modules happen
    # to spell them out.
    unread.assert_no_dataclass_field_goes_unread(
        ROOT, SCANNED,
        allowing=("CommandFiles.portrait_hud_file", "CommandFiles.landscape_hud_file"))


def test_every_declared_command_line_option_is_read():
    unread.assert_every_argparse_option_is_read(ROOT, SCANNED)


def test_no_test_helper_is_written_and_never_called():
    unread.assert_no_test_helper_is_written_and_never_called(ROOT, ROOT / "tests")


def test_no_module_reaches_into_another_ones_privates():
    """A leading underscore is the only boundary marker these packages have.

    Six imports crossed it — two banding helpers, the event log, the HUD
    priming, and a voice-control error string read from two other modules —
    which means the name said "mine" while five call sites in three packages
    depended on it.  Either a name is part of a module's surface, in which case
    it says so and carries a docstring, or it is not and nobody outside reads
    it; this is what makes that a rule rather than an intention.
    """
    reaches = []
    for path in _package_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            for alias in node.names:
                if alias.name.startswith("_") and not alias.name.startswith("__"):
                    module = node.module or "." * node.level
                    reaches.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: "
                        f"from {module} import {alias.name}")

    assert reaches == [], (
        "a private name is imported across a module boundary:\n" + "\n".join(reaches)
        + "\nEither make it public with a docstring saying what it promises, "
          "or stop reading it from outside."
    )



# How many lines of the packages are prose rather than code, as measured by
# _prose_and_code below. A RATCHET: lower it when prose goes, never raise it.
# The audit that set it measured 0.46 prose per line of code against a ~0.25
# norm, with the reasoning for the design living in comments rather than in
# names and tests -- which is how a docstring came to cite a module that had
# been deleted and a comment came to promise a seventh child that already
# existed.
#
# A COUNT, not that ratio, since 2026-08-31. Held as a ratio it fired on any
# small edit in EITHER direction once the number sat near its cap: deleting a
# line of dead code -- the thing every other gate in this file exists to force
# -- raised the ratio and failed the build, pointing the author at prose they
# had never touched. What the gate is for is prose that outgrows what it
# explains, and that is what this counts.
# 8054 since 2026-09-18, when the VR controllers stick stopped tilting the
# scene and the reader that combined the two sticks gave its docstring to a
# test class.
# 8058 since 2026-09-18, when the VR main player learned its scenes and the
# forwarder that carries them stopped restating the test that pins it.
# 8123 since 2026-09-16, measured on the merged tree: every player began
# remembering where each video was left, and the memory arrived as constants and
# tests rather than paragraphs -- paying its own way out of the session's
# duplicates, the unscripted-video sentence its two owners already carried and
# the volume bounds its property restates.
# 8148 since 2026-09-15, measured on the merged tree: Fun Time declares the
# players' buttons now, and the paragraphs the main player and the satellites
# had folding the fixed rows in went with them.
# 8171 since 2026-09-15, measured on the merged tree: the regenerate URL
# stopped reading a list of Evolver's stamps and asked the sidecar for a prompt,
# and the paragraphs defending that list and the resolver's old eligibility
# went with it.
# 8187 since 2026-09-13, when Esc came to call off what every cover covers and a
# crossing's way back said so in names and tests.
# 8188 since 2026-09-13, when the hotkey script stayed up over the closing cover
# and stopping it no longer claimed to be how every session ends.
# 8190 since 2026-09-13, when what Esc cancels became the words on a cover's own
# progress line, and the cancel object each screen built, with its four
# docstrings, went.
# 8202 since 2026-09-13, measured on the merged tree: startup stopped putting
# the loading cover back on top after every raise and banded the room under it
# instead, and the paragraphs explaining the put-back went with it.
# 8225 since 2026-09-13, measured on the merged tree: the notice colors took the
# two notice-level aliases out, with the paragraphs that justified them.
# 8234 since 2026-09-13, when the VR screens went flat and the paragraphs
# explaining the curve and its flat stand-in went with it.
# 8249 since 2026-09-13, when the satellite transport's docstring went rather than
# grow a clause for the loop down to one clip that Right now leaves.
# 8250 since 2026-09-12, when the bridge log's append handler lost the paragraph
# claiming the hotkey script needed the file to itself -- the script now opens it
# shared.
# 8255 since 2026-09-12, when the main player moved in from genau
# under its new name with its own prose: the count grew by a package, not by a
# paragraph, and from here it comes down again as before.
# 6898 since 2026-09-12, when the desktop's startup stopped carrying the
# headset's shape filter and the comment claiming it served the headset went.
# 6903 since 2026-09-12, when force_foreground_window's docstring stopped
# promising an activation that a desktop with no foreground never delivered.
# 6904 since 2026-09-07, measured on the merged tree: the audit stack replaced
# eleven ambient switch reads with one record, gave the lock plan's four
# outcomes four named constructors and the main slot's two modes their names,
# and each of those took the paragraph that had explained it -- one of them
# duplicated word for word by the test that pins it.
# 6916 since 2026-09-07, when the VR covers landed and the contract the cover
# family had each restated -- the two Esc routes, the flag a cover follows, the
# ready flag both ends derive -- collapsed into overlay_progress alone.
# 6917 since 2026-09-06, when the bug wave's two new dispatch functions went in
# under their own names, and the anchor fix's paragraph shrank to the sentence
# left true.  6919 the same day, when the sidecar writer took the family's lock
# and the paragraph explaining its hand-rolled rename went with the rename.
# 6922 earlier that day, when the library browser's own additions went in as
# names and tests rather than docstrings.  6923 earlier the same day, when the
# VR pointer landed with less prose than it found.  6925 since 2026-09-03: the commits main took between the last ratchet and the
# audit stack's landing (the hosted app in the suite, the sibling pin, the VR
# icon) brought their prose with them, through the ratio gate that held main
# then; the count ratchets down from the merged tree, not from either side.
# 8118 since 2026-09-16, when the room stopped waiting out the hosted app's
# boot: the wait, its timeout, its phase and every "a session that opens in
# origenerator mode" branch went with it.
# 8114 since 2026-09-16, measured on the merged tree: each headset video moved
# to a thread of its own, and the paragraphs describing mpv rendering on the
# frame loop went with it.
# 8076 since 2026-09-18, when the library browser sidebar became letters that
# open onto their names, and its docstrings shrank to what that leaves true.
# 8072 since 2026-09-18, when a spoken command OmniPause drops began saying so
# on screen, and the docstring listing what voice flashes gave way to the tests.
# 8049 since 2026-09-18, when the headset dashboard came back to a default
# spot a test measures, and the paragraph that had worked it out went.
# 8041 since 2026-09-18, measured on the merged tree: the VR screens stopped
# being drawn in one fixed order and the docstrings reciting that order went,
# and the hotkey reference gave up its own spot to hang from the dashboard.
# 7937 since 2026-09-19, when the headset's own library browser gave way to the
# desktop's: a panel, a painter and their tests went, and what is left is the
# process that runs the desktop window with no window.
# 7930 since 2026-09-19, measured on the merged tree, when Genau's startup
# placement stopped landing late: two comments in the startup pass that
# restated the docstrings under them went, one of them repeating the stall
# guard's claim that a call it gives up on is dropped.
# 7928 since 2026-09-19, measured on the merged tree: an Origenerator already
# open has its mode opened at once, in names and tests.
# 7916 since 2026-09-19, measured on the merged tree, when the side players
# learned their clips' other renditions: the satellite session's list, discard
# and versions said in names and tests what three paragraphs had said, and the
# note over the map's keyboard steps stopped counting four of them.
# 7914 since 2026-09-19, measured on the merged tree, when the main player
# joined the settle walk: which players it watches, and what each may sit
# under, is a helper's name and three tests rather than a paragraph.
# 7912 since 2026-09-20, when the control bar learned its own width: which
# rectangles it places, and that the width counts them all, is a property's name
# and two tests rather than a paragraph over the field it used to name.
# 7911 since 2026-09-20, when the headset stopped offering minimize, and the
# switch that left the console's mode row off, which no caller used, went.
# 7908 since 2026-09-21, when twenty comment blocks stopped replaying the
# incident that produced the rule and said the rule instead, in the present
# tense -- and the imports that stayed local said why in one line each.
# 7828 since 2026-09-21, when the resume and the branch-seed stopped restating
# docs/resuming-a-session.md and their own test names: eleven docstrings and two
# comment blocks said what a named test already asserts, and the four flags a
# resumed session re-asserts are written down in one place rather than two.
# 7827 the same day: what a satellite is launched with is one record rather than
# three declarations, so the paragraphs telling each reader about the other two
# are the record's name.
# 7824 on 2026-09-25: the main lock's docstring said which player it reaches,
# which the lock's own tests in test_command_dispatch.py are named for.
# 7805 the same day: the main player's window stopped answering keys of its
# own, Fun Time's hotkeys being the room's keyboard, so the table of them went.
# 7787 the same day: the heatmap strip builds its colors across the track of
# the frame it is handed, so the painter's paragraph telling its caller to do
# that went, and the plain bar standing in for it is timeline_bgra's own branch.
# 7786 the same day: what the headset's scrubber repaints on is the Scrubber's
# state, held to the bar it draws by a test rather than a docstring.
# 7772 the same day: the heatmap's fill is the shared scrubber's, so the main
# player's copy of that frame and its paragraphs went, and the side screens stopped
# being described as unscripted now that they color their bars too.
MAX_PROSE_LINES = 7772


def _prose_and_code(path: Path) -> tuple[int, int]:
    """(prose lines, code lines) in one module.

    A line is prose when its only content is a comment or a docstring, so a
    trailing `# why` on a real statement costs nothing -- the ratio is about
    paragraphs, not annotations.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    kind = ["blank" if not line.strip() else "code" for line in lines]
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):  # pragma: no cover - syntax is CI's job
        return 0, 0

    def mark(token, *, only_if_alone: bool):
        for n in range(token.start[0] - 1, token.end[0]):
            if kind[n] != "code":
                continue
            if only_if_alone and lines[n].split("#")[0].strip():
                continue  # a trailing `# why` on a statement is not prose
            kind[n] = "prose"

    opens_a_statement = tokenize.INDENT
    for token in tokens:
        if token.type == tokenize.COMMENT:
            mark(token, only_if_alone=True)
        elif token.type == tokenize.STRING and opens_a_statement in (
            tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL,
        ):
            mark(token, only_if_alone=False)
        if token.type not in (tokenize.COMMENT, tokenize.NL):
            opens_a_statement = token.type
    return kind.count("prose"), kind.count("code")


def test_prose_does_not_outgrow_the_code_it_explains():
    """A ceiling that can only come down.

    Design that lives in prose has to be kept in step by hand, and is not kept
    in step by hand. Where a paragraph states a rule, the way to spend it is a
    name or a test, not another paragraph -- and this fails until one of those
    is what carries it.
    """
    prose = code = 0
    for path in _package_sources() + sorted((ROOT / "tools").rglob("*.py")):
        module_prose, module_code = _prose_and_code(path)
        prose += module_prose
        code += module_code

    assert prose <= MAX_PROSE_LINES, (
        f"{prose} prose lines (ceiling {MAX_PROSE_LINES}), against {code} of code "
        f"-- {prose / code:.4f} per line. Delete a stale block, or move what it "
        "says into a name or a test; lower MAX_PROSE_LINES when you do."
    )


# Every module that reaches ``ctypes``' Windows binding surface directly rather
# than through ``fun_time.win32_loader``, and how many times.  Held as an
# EQUALITY per file: a count that has come down means a reach was consolidated
# and this table was not lowered with it, and a file that is absent must have
# none at all.  This is the number no length or coverage gate can see — the
# dashboard carried eleven of these inside a QMainWindow constructor, with
# GWL_STYLE written as a bare -16 and the style bits as inline hex, while
# fun_time/win32.py existed to be the place that names them.
_WIN32_REACHES = {
    # The loader itself: this IS the binding, and the thing it exists to be
    # asked instead.
    "fun_time/win32_loader.py": 5,
    # Deliberate: the stand-in raises Win32Unavailable (a RuntimeError) where
    # these want the AttributeError ctypes.windll gives off Windows, and every
    # caller catches it to fall back rather than to fail.  monitors.py is the
    # module the rest of the family asks for a monitor rather than measuring
    # one, which is why its count is the only one that ever goes up.
    "fun_time/monitors.py": 13,
}

# What counts as reaching it.
_WIN32_BINDING_NAMES = frozenset(
    {"windll", "oledll", "WinDLL", "OleDLL", "CDLL", "PyDLL", "WINFUNCTYPE"})


def _win32_reaches(tree) -> int:
    """How many CALLS this module makes past the layer, not how often it names it.

    A count of names is walked around by one hoist: ``u = ctypes.windll.user32``
    and then eleven ``u.X()`` scores 1, with none of the guard, none of the named
    constants and no trip through fun_time.win32 -- which is exactly the shape
    this exists to catch. So a local bound to the binding surface is followed,
    and every call THROUGH it counts.

    The spellings that mean the same thing all count too: ``ctypes.windll``,
    ``import ctypes as c`` then ``c.windll``, ``c = ctypes`` then ``c.windll``,
    ``from ctypes import windll``, and ``getattr(ctypes, "windll")``.
    """
    ctypes_names = {"ctypes"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "ctypes" and alias.asname:
                    ctypes_names.add(alias.asname)
        elif (isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
                and node.value.id in ctypes_names):
            ctypes_names.update(
                t.id for t in node.targets if isinstance(t, ast.Name))

    def names_the_surface(node) -> bool:
        """Whether this expression IS ctypes' binding surface, however spelled."""
        if isinstance(node, ast.Attribute):
            if node.attr in _WIN32_BINDING_NAMES and isinstance(node.value, ast.Name):
                return node.value.id in ctypes_names
            return names_the_surface(node.value)      # ctypes.windll.user32
        if isinstance(node, ast.Call):
            if (isinstance(node.func, ast.Name) and node.func.id == "getattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id in ctypes_names
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in _WIN32_BINDING_NAMES):
                return True
            return names_the_surface(node.func)       # ctypes.WinDLL("user32")
        if isinstance(node, ast.Name):
            return node.id in bound_to_surface
        return False

    # Locals holding a DLL taken off that surface: the hoist.
    bound_to_surface: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and names_the_surface(node.value):
            bound_to_surface.update(
                t.id for t in node.targets if isinstance(t, ast.Name))

    reaches = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "ctypes":
            reaches += sum(1 for a in node.names if a.name in _WIN32_BINDING_NAMES)
        elif isinstance(node, ast.Call) and names_the_surface(node.func):
            reaches += 1                               # a call through it
        elif isinstance(node, ast.Attribute) and node.attr in _WIN32_BINDING_NAMES:
            if isinstance(node.value, ast.Name) and node.value.id in ctypes_names:
                reaches += 1                           # named, not yet called
    return reaches


def test_nothing_new_binds_win32_under_the_layer_that_exists_to():
    """A ceiling on coupling, not on size, held per file and as an equality.

    ``fun_time/win32.py`` wraps every cross-process window call in a guard
    after a stalled player once wedged a whole session, and names the constants
    the family reads to learn its Win32 conventions.  A module that goes to
    ``ctypes.windll`` instead gets neither, and nothing said so: the dashboard
    made eleven such calls, five of the thirteen constants it restated inline
    were already exported next door, and every length and coverage gate in this
    repo passed the whole time.
    """
    counted = {}
    for path in _package_sources():
        reaches = _win32_reaches(ast.parse(path.read_text(encoding="utf-8")))
        if reaches:
            counted[path.relative_to(ROOT).as_posix()] = reaches

    assert counted == _WIN32_REACHES, (
        "the direct-binding count moved.  A file that is new here reaches "
        "ctypes.windll instead of asking fun_time.win32 or fun_time.monitors; "
        "a count that came down is one to lower here in the same commit."
    )
