"""The INI a session reads its own mode off."""
from __future__ import annotations

import ast
import configparser
import subprocess
import sys
from dataclasses import fields, replace
from pathlib import Path

import pytest

from fun_time import shared_state
from fun_time.mode_plan import MAIN_MODES, MAIN_VIDEO_MODE
from fun_time.players import Player
from fun_time.satellites_mode import VIDEO_MODE as SATELLITES_VIDEO_MODE
from fun_time.shared_state import (
    SHARED_STATE_FILENAME,
    BridgeState,
    SideState,
    read_shared_state,
    shared_state_path,
    write_shared_state,
)


def test_the_state_file_is_named_off_the_state_dir(tmp_path: Path):
    """Four processes open this file by path, so they resolve it one way."""
    assert shared_state_path(tmp_path) == tmp_path / SHARED_STATE_FILENAME


def _shifted(value):
    """Any value of the same type that is not *value* — so a field which fails to
    round-trip comes back visibly wrong rather than accidentally right.  Only
    difference matters here: the file is a transport, and what counts as a legal
    volume or side is the dispatch's business, not this INI's."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    return f"{value}-carried"


def _shifted_state(state: BridgeState) -> BridgeState:
    """*state* with every value it holds replaced by a different one of its own
    type — both satellites' own among them, which is why this walks the sides
    rather than the top-level fields alone."""
    shifted = replace(state, **{
        f.name: _shifted(getattr(state, f.name)) for f in fields(state)
        if not isinstance(getattr(state, f.name), SideState)})
    for player in Player.SATELLITES:
        side = shifted.side(player)
        shifted = shifted.with_side(
            player, **{f.name: _shifted(getattr(side, f.name)) for f in fields(side)})
    return shifted


def test_every_field_of_the_state_survives_the_round_trip(tmp_path: Path):
    """The dispatch loop replaces its whole state with what this file reads back,
    every tick.  So a field written by a command but missing from the INI is not
    merely unsaved — it is undone a fraction of a second later, while the toast
    that acknowledged it is still on screen.  That is what "main latest" did: the
    playlist was rebuilt newest-first and the flag was reset before any HUD drew it.

    Hence the whole dataclass rather than a field at a time: every one of these is
    session mode, and the next one added has to be carried too.
    """
    state_file = tmp_path / "shared_state.ini"
    state = _shifted_state(BridgeState())

    write_shared_state(state_file, state)

    assert read_shared_state(state_file) == state


class TestSharedState:
    def test_write_then_read_roundtrip(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(
            portrait=SideState(locked=True),
            landscape=SideState(f_mode=True),
            main_mode="genau",
            main_f_mode=True,
            omni_paused=True,
        )

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded == state

    def test_read_returns_none_when_missing(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        assert read_shared_state(state_file) is None

    def test_active_side_roundtrips(self, tmp_path):
        """active_side must persist: tick() reloads state from this file every
        iteration, so a side set by a nav command would be lost otherwise."""
        state_file = tmp_path / "shared_state.ini"
        write_shared_state(state_file, BridgeState(active_side=3))

        loaded = read_shared_state(state_file)

        assert loaded is not None
        assert loaded.active_side == 3

    def test_active_side_defaults_to_the_primary_for_legacy_files(self, tmp_path):
        """An INI written before active_side existed loads as the main player (1) —
        the same floor a fresh session opens on."""
        state_file = tmp_path / "shared_state.ini"
        state_file.write_text(
            "[state]\nlocked2 = 0\nlocked3 = 0\nprimary_mode = nau\n"
            "omni_paused = 0\n",
            encoding="utf-8",
        )

        loaded = read_shared_state(state_file)

        assert loaded is not None
        assert loaded.active_side == 1

    def test_roundtrip_preserves_the_sound_level(self, tmp_path):
        """volume/muted must persist: tick() reloads state from this file every
        iteration, so a spoken "quieter" would be undone before it was heard."""
        state_file = tmp_path / "shared_state.ini"

        write_shared_state(state_file, BridgeState(volume=30, muted=True))
        loaded = read_shared_state(state_file)

        assert loaded.volume == 30
        assert loaded.muted is True

    def test_roundtrip_preserves_per_satellite_filters(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(
            main_mode="video",
            portrait=SideState(filter="beta gamma"),
            landscape=SideState(filter="alpha"),
        )

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded.side(Player.PORTRAIT).filter == "beta gamma"
        assert loaded.side(Player.LANDSCAPE).filter == "alpha"

    def test_roundtrip_preserves_per_satellite_loops(self, tmp_path):
        """The HUD runs in its own process and reads its loop state from this
        file, so a loop set by a command has to survive the round-trip."""
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(portrait=SideState(loop="seed"),
                            landscape=SideState(loop="action"))

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded.side(Player.PORTRAIT).loop == "seed"
        assert loaded.side(Player.LANDSCAPE).loop == "action"

    def test_roundtrip_preserves_the_map_anchor(self, tmp_path):
        """The HUD orders a running loop's map from the clip the loop started on,
        which it reads from this file, so it must survive the round-trip."""
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(portrait=SideState(map_anchor="C:/v/a.mp4"),
                            landscape=SideState(map_anchor="C:/v/b.mp4"))

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded.side(Player.PORTRAIT).map_anchor == "C:/v/a.mp4"
        assert loaded.side(Player.LANDSCAPE).map_anchor == "C:/v/b.mp4"

    def test_roundtrip_preserves_the_widen_clip(self, tmp_path):
        """The HUD reads which clip each side's seed row is widened around from
        this file, so it must survive the round-trip."""
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(portrait=SideState(widen_clip="C:/v/a.mp4"),
                            landscape=SideState(widen_clip="C:/v/b.mp4"))

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded.side(Player.PORTRAIT).widen_clip == "C:/v/a.mp4"
        assert loaded.side(Player.LANDSCAPE).widen_clip == "C:/v/b.mp4"

    def test_roundtrip_preserves_the_nav_anchor(self, tmp_path):
        """The HUD reads which clip each side's map is frozen on for keyboard
        navigation from this file, so it must survive the round-trip."""
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(portrait=SideState(nav_anchor="C:/v/a.mp4"),
                            landscape=SideState(nav_anchor="C:/v/b.mp4"))

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded.side(Player.PORTRAIT).nav_anchor == "C:/v/a.mp4"
        assert loaded.side(Player.LANDSCAPE).nav_anchor == "C:/v/b.mp4"

    def test_state_files_without_loop_keys_load_as_unlooped(self, tmp_path):
        # A state file written before loops were tracked must still load.
        state_file = tmp_path / "shared_state.ini"
        state_file.write_text(
            "[state]\nlocked2 = 0\nlocked3 = 0\nprimary_mode = nau\n"
            "omni_paused = 0\n",
            encoding="utf-8",
        )

        loaded = read_shared_state(state_file)

        assert loaded is not None
        assert loaded.side(Player.PORTRAIT).loop == ""
        assert loaded.side(Player.LANDSCAPE).loop == ""

    def test_state_files_without_filter_keys_load_as_unfiltered(self, tmp_path):
        # A state file written before filters existed must still load.
        state_file = tmp_path / "shared_state.ini"
        state_file.write_text(
            "[state]\nlocked2 = 0\nlocked3 = 0\nprimary_mode = nau\n"
            "omni_paused = 0\n",
            encoding="utf-8",
        )

        loaded = read_shared_state(state_file)

        assert loaded is not None
        assert loaded.side(Player.PORTRAIT).filter == ""
        assert loaded.side(Player.LANDSCAPE).filter == ""


def test_reading_the_state_file_does_not_drag_in_the_dispatcher():
    """One small INI, and importing its reader pulled in 28 of this package's
    modules — the whole dispatcher among them, which is the repo's hottest and
    most complex file, because `BridgeState` lived there and this module
    imported it.

    Startup reads this file before the dispatch loop exists, and the dashboard
    and both satellite HUDs read it from their own processes; none of them wants
    the command vocabulary.  A ceiling that can only come down.
    """
    probe = (
        "import sys, fun_time.shared_state; "
        "print('fun_time.command_dispatch' in sys.modules); "
        "print(len([m for m in sys.modules if m.startswith('fun_time.')]))"
    )
    result = subprocess.run([sys.executable, "-c", probe],
                            capture_output=True, text=True, check=True)
    pulls_dispatcher, count = result.stdout.split()

    assert pulls_dispatcher == "False", (
        "fun_time.shared_state imports the dispatcher again")
    assert int(count) <= 10, (
        f"reading the shared state file now imports {count} fun_time modules; "
        "lower this ceiling when it drops, never raise it")


def test_the_round_trip_spells_no_field_of_the_record_by_hand():
    """These names used to be written out four times over: as the record's
    fields, as the keys the writer emits, as the keys the reader asks for, and
    as the list a resume carries.  Nothing tied the four together, so a field
    added to the record and forgotten in the writer was never saved at all, and
    one forgotten in the reader came back at its default on every resume -- which
    is how a HUD once described a session other than the one playing.  They are
    derived from the record now, and this is what keeps them that way.

    The two mode words are the exception and are named on purpose: a session
    saved under a name the app has since dropped has to be translated to the mode
    that name became, which is a fact about those two values and no others.
    """
    spelled_out = sorted(
        {node.value for node in ast.walk(ast.parse(
            Path(shared_state.__file__).read_text(encoding="utf-8")))
         if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        & {f.name for f in fields(BridgeState)})

    assert spelled_out == ["main_mode", "satellites_mode"], (
        "the round trip names these fields by hand: " + ", ".join(spelled_out)
        + " — derive them from the record instead, so one added to it is carried."
    )


def test_no_satellite_value_is_a_field_of_the_whole_state():
    """A value one satellite carries belongs to :class:`SideState`, reached
    through :meth:`BridgeState.side` — never to a field of its own beside the
    session's.  Held because the pair used to answer to three names at once:
    ``which`` 2 and 3 in signatures, ``locked2``/``locked3`` in the record,
    ``portrait``/``landscape`` everywhere a person could read it, and forty-one
    conditionals in between whose only job was to translate.  Every one of those
    was a place a 2 could be written where a 3 belonged.  The legacy spellings
    survive as INI keys, which is a wire format and a different thing.
    """
    stray = [f.name for f in fields(BridgeState)
             if f.name.startswith(("portrait_", "landscape_"))
             or f.name[-1:] in ("2", "3")]

    assert stray == [], (
        "these say a satellite's name in the state record: " + ", ".join(stray)
        + " — put them on SideState and reach them through BridgeState.side."
    )


class TestTheSideLens:
    """BridgeState.side / with_side — how a caller holding one satellite reaches
    that satellite's state and nothing else."""

    def test_side_reads_the_state_of_that_satellite_alone(self):
        state = BridgeState(
            portrait=SideState(
                locked=True, filter="alpha", f_mode=True, latest=True, loop="seed",
                map_anchor="a.mp4", widen_clip="w.mp4", nav_anchor="n.mp4"),
            landscape=SideState(filter="beta gamma"),
        )
        portrait = state.side(Player.PORTRAIT)
        assert (portrait.locked, portrait.filter, portrait.f_mode) == (True, "alpha", True)
        assert (portrait.latest, portrait.loop) == (True, "seed")
        assert (portrait.map_anchor, portrait.widen_clip, portrait.nav_anchor) == (
            "a.mp4", "w.mp4", "n.mp4")
        landscape = state.side(Player.LANDSCAPE)
        assert landscape.filter == "beta gamma"
        assert landscape.locked is False

    def test_with_side_leaves_the_other_satellite_where_it_was(self):
        state = BridgeState(landscape=SideState(filter="delta"))

        state = state.with_side(Player.PORTRAIT, locked=True, loop="action",
                                map_anchor="x.mp4")

        assert state.side(Player.PORTRAIT) == SideState(
            locked=True, loop="action", map_anchor="x.mp4")
        assert state.side(Player.LANDSCAPE) == SideState(filter="delta")

    def test_with_side_refuses_a_name_no_satellite_carries(self):
        """Every caller names the value it sets, so a misspelling is a write that
        goes nowhere at all unless the record refuses it."""
        with pytest.raises(TypeError):
            BridgeState().with_side(Player.PORTRAIT, lokced=True)

    def test_a_default_side_state_means_the_side_sits_at_its_defaults(self):
        assert BridgeState().side(Player.LANDSCAPE) == SideState()
        assert BridgeState(landscape=SideState(loop="seed")).side(
            Player.LANDSCAPE) != SideState()

    def test_the_lens_survives_the_ini_round_trip(self, tmp_path):
        state_file = tmp_path / "shared_bridge_state.ini"
        state = BridgeState().with_side(
            Player.LANDSCAPE, filter="delta", f_mode=True, latest=True)

        write_shared_state(state_file, state)

        assert read_shared_state(state_file).side(Player.LANDSCAPE) == state.side(
            Player.LANDSCAPE)


def test_a_state_saved_before_video_mode_comes_back_in_video_mode(tmp_path: Path):
    """The main slot's nau and hybrid modes became the one video mode, and the
    satellites' player mode was renamed to match — a session that last ran
    under the old names has to come back in a mode the room still knows."""
    state_file = tmp_path / SHARED_STATE_FILENAME
    for saved_main, saved_satellites in (("nau", "player"), ("hybrid", "player")):
        write_shared_state(state_file, BridgeState())
        text = state_file.read_text(encoding="utf-8")
        text = text.replace("main_mode = video", f"main_mode = {saved_main}")
        text = text.replace("satellites_mode = video", f"satellites_mode = {saved_satellites}")
        state_file.write_text(text, encoding="utf-8")

        resumed = read_shared_state(state_file)

        assert (resumed.main_mode, resumed.satellites_mode) == ("video", "video"), saved_main


def test_a_state_file_from_before_the_rename_comes_back_in_a_mode_that_exists(tmp_path: Path):
    """The main slot's nau and hybrid modes became one video mode, and the
    satellites' player mode was renamed to match.  A file saved then has to come
    back as the mode those are now: an unrecognized one used to answer False to
    every question and quietly park the players, and now build_mode_switch_plan
    refuses it outright, so a resumed session would not switch at all.
    """
    state_file = tmp_path / SHARED_STATE_FILENAME
    for saved in ("nau", "hybrid"):
        write_shared_state(state_file, BridgeState(main_mode=saved))
        assert read_shared_state(state_file).main_mode == MAIN_VIDEO_MODE

    write_shared_state(state_file, BridgeState(satellites_mode="player"))
    assert read_shared_state(state_file).satellites_mode == SATELLITES_VIDEO_MODE


def test_a_mode_this_app_still_has_is_read_back_unchanged(tmp_path: Path):
    state_file = tmp_path / SHARED_STATE_FILENAME
    for mode in MAIN_MODES:
        write_shared_state(state_file, BridgeState(main_mode=mode))
        assert read_shared_state(state_file).main_mode == mode


# Every key the file carries, and the value a default session writes for it.
# Derived from the dataclass since the round trip stopped spelling them by
# hand — so this is where a field rename becomes a deliberate, visible act,
# the way tests/test_manifest.py holds the launch manifest's inventory.
_EXPECTED_STATE_KEYS = {
    "locked2": "0", "locked3": "0",
    "main_mode": "video", "satellites_mode": "video",
    "main_f_mode": "0", "portrait_f_mode": "0", "landscape_f_mode": "0",
    "omni_paused": "0",
    "main_latest": "0", "portrait_latest": "0", "landscape_latest": "0",
    "main_plays_vr": "1", "main_plays_flat": "1",
    "genau_latest": "0",
    "active_side": "1",
    "portrait_filter": "", "landscape_filter": "",
    "portrait_loop": "", "landscape_loop": "",
    "portrait_map_anchor": "", "landscape_map_anchor": "",
    "portrait_widen_clip": "", "landscape_widen_clip": "",
    "portrait_nav_anchor": "", "landscape_nav_anchor": "",
    "volume": "100", "muted": "0",
}


def test_the_file_carries_exactly_these_keys_spelled_exactly_this_way(tmp_path: Path):
    """The dashboard and the resume both read this file by key name, so a
    respelling is a session that comes back at its defaults with nothing said.
    A key added, dropped or renamed must be added, dropped or renamed here in
    the same commit.
    """
    state_file = tmp_path / SHARED_STATE_FILENAME
    write_shared_state(state_file, BridgeState())

    parser = configparser.ConfigParser()
    parser.optionxform = str
    parser.read(state_file, encoding="utf-8")

    assert dict(parser["state"]) == _EXPECTED_STATE_KEYS
