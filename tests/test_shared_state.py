"""The INI a session reads its own mode off."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import fields, replace
from pathlib import Path

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
    state = BridgeState()
    state = replace(state, **{f.name: _shifted(getattr(state, f.name)) for f in fields(state)})

    write_shared_state(state_file, state)

    assert read_shared_state(state_file) == state


class TestSharedState:
    def test_write_then_read_roundtrip(self, tmp_path):
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(
            locked2=True,
            locked3=False,
            main_mode="genau",
            main_f_mode=True,
            portrait_f_mode=False,
            landscape_f_mode=True,
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
            main_mode="nau",
            portrait_filter="beta gamma",
            landscape_filter="alpha",
        )

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded.portrait_filter == "beta gamma"
        assert loaded.landscape_filter == "alpha"

    def test_roundtrip_preserves_per_satellite_loops(self, tmp_path):
        """The HUD runs in its own process and reads its loop state from this
        file, so a loop set by a command has to survive the round-trip."""
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(portrait_loop="seed", landscape_loop="action")

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded.portrait_loop == "seed"
        assert loaded.landscape_loop == "action"

    def test_roundtrip_preserves_the_map_anchor(self, tmp_path):
        """The HUD orders a running loop's map from the clip the loop started on,
        which it reads from this file, so it must survive the round-trip."""
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(portrait_map_anchor="C:/v/a.mp4", landscape_map_anchor="C:/v/b.mp4")

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded.portrait_map_anchor == "C:/v/a.mp4"
        assert loaded.landscape_map_anchor == "C:/v/b.mp4"

    def test_roundtrip_preserves_the_widen_clip(self, tmp_path):
        """The HUD reads which clip each side's seed row is widened around from
        this file, so it must survive the round-trip."""
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(portrait_widen_clip="C:/v/a.mp4", landscape_widen_clip="C:/v/b.mp4")

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded.portrait_widen_clip == "C:/v/a.mp4"
        assert loaded.landscape_widen_clip == "C:/v/b.mp4"

    def test_roundtrip_preserves_the_nav_anchor(self, tmp_path):
        """The HUD reads which clip each side's map is frozen on for keyboard
        navigation from this file, so it must survive the round-trip."""
        state_file = tmp_path / "shared_state.ini"
        state = BridgeState(portrait_nav_anchor="C:/v/a.mp4", landscape_nav_anchor="C:/v/b.mp4")

        write_shared_state(state_file, state)
        loaded = read_shared_state(state_file)

        assert loaded.portrait_nav_anchor == "C:/v/a.mp4"
        assert loaded.landscape_nav_anchor == "C:/v/b.mp4"

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
        assert loaded.portrait_loop == ""
        assert loaded.landscape_loop == ""

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
        assert loaded.portrait_filter == ""
        assert loaded.landscape_filter == ""


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


class TestTheSideLens:
    """BridgeState.side / with_side — the one crossing between the flat
    portrait_/landscape_ fields (which the INI keeps, so the dashboard and both
    satellite HUD processes go on reading the same keys) and the per-side view
    every reader and writer actually wants."""

    def test_side_reads_the_flat_fields_of_that_side_alone(self):
        state = BridgeState(
            locked2=True, portrait_filter="alpha", portrait_f_mode=True,
            portrait_latest=True, portrait_loop="seed",
            portrait_map_anchor="a.mp4", portrait_widen_clip="w.mp4",
            portrait_nav_anchor="n.mp4",
            landscape_filter="beta gamma",
        )
        portrait = state.side(2)
        assert (portrait.locked, portrait.filter, portrait.f_mode) == (True, "alpha", True)
        assert (portrait.latest, portrait.loop) == (True, "seed")
        assert (portrait.map_anchor, portrait.widen_clip, portrait.nav_anchor) == (
            "a.mp4", "w.mp4", "n.mp4")
        landscape = state.side(3)
        assert landscape.filter == "beta gamma"
        assert landscape.locked is False

    def test_with_side_writes_only_that_sides_flat_fields(self):
        state = BridgeState(landscape_filter="delta")

        state = state.with_side(2, locked=True, loop="action", map_anchor="x.mp4")

        assert (state.locked2, state.portrait_loop, state.portrait_map_anchor) == (
            True, "action", "x.mp4")
        assert (state.locked3, state.landscape_filter) == (False, "delta")

    def test_a_default_side_state_means_the_side_sits_at_its_defaults(self):
        assert BridgeState().side(3) == SideState()
        assert BridgeState(landscape_loop="seed").side(3) != SideState()

    def test_the_lens_survives_the_ini_round_trip(self, tmp_path):
        state_file = tmp_path / "shared_bridge_state.ini"
        state = BridgeState().with_side(3, filter="delta", f_mode=True, latest=True)

        write_shared_state(state_file, state)

        assert read_shared_state(state_file).side(3) == state.side(3)
