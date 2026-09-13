"""The buttons Fun Time puts on a satellite's HUD, declared in
:class:`player_core.hud_button.Button` off the player's own state.  Each verb is
the dashboard command the dispatcher answers for that player; a mode button's
names no player, the mode belonging to both satellites at once."""
from __future__ import annotations

from player_core.hud_button import FIT_THE_WORD, Button
from player_core.hud_marks import FMODE_ICON, MINIMIZE_ICON, shared_mark
from player_core.hud_status import LATEST_LABEL, SHUFFLE_LABEL
from player_core.modes import SatellitesMode

# The same groups, in the same order, the console's rows are cut into.
CONTROL_GROUPS = (
    ("prev", "next"),
    ("lock", "trash", "fmode"),
    ("reset",),
    ("shuffle", "latest"),
    ("minimize",),
)
_GROUP_OF = {name: index for index, group in enumerate(CONTROL_GROUPS) for name in group}
_ORDER_CONTROLS = ("shuffle", "latest")

MODE_BUTTONS = (
    ("satellites_video_activate", "Video", SatellitesMode.VIDEO),
    ("origenerator_activate", "Origenerator", SatellitesMode.ORIGENERATOR),
)

CONTROL_TOOLTIPS = {
    "prev": "Previous clip",
    "next": "Next clip",
    "lock": "Lock / unlock this clip",
    "trash": "Unfavorite it — or mark weird when it is not a favorite",
    "fmode": "F-Mode — browse only the favorites on this player",
    "reset": "Reset — no filter, no lock, no loop, no F-Mode, shuffled from the top",
    "shuffle": f"{SHUFFLE_LABEL} — reshuffle this player's browse",
    "latest": f"{LATEST_LABEL} — reload this player's browse newest-first",
    "minimize": "Minimize this player — bring it back from the taskbar",
}
MODE_TOOLTIPS = {
    "satellites_video_activate": "Video mode — the satellite players and the Random Favs Browser",
    "origenerator_activate":
        "Origenerator mode — Origenerator over the browser, its shows over the players",
}
# The hover a dim Origenerator button gives instead: the room opens without
# waiting out that app's boot, and a hover over a button that cannot be pressed
# has to say why.
STILL_STARTING_TOOLTIP = "Origenerator is still starting — this lights up when it is ready"
CONTROL_FACES = {
    "prev": "⏮", "next": "⏭", "lock": "🔒",
    "trash": shared_mark("trash"), "reset": shared_mark("reset"),
    "shuffle": shared_mark("shuffle"), "latest": shared_mark("latest"),
    "fmode": FMODE_ICON, "minimize": MINIMIZE_ICON,
}


def player_rows(player: str, *, locked: bool = False, favorites_filter: bool = False,
                latest: bool | None = None,
                satellites_mode: SatellitesMode | None = None,
                origenerator_ready: bool = True,
                nothing_to_reset: bool = False) -> tuple[tuple[Button, ...], ...]:
    names = [name for group in CONTROL_GROUPS for name in group]
    if latest is None:
        names = [name for name in names if name not in _ORDER_CONTROLS]
    rows: list[tuple[Button, ...]] = []
    if satellites_mode is not None:
        names.remove("minimize")
        rows.append(mode_row(player, satellites_mode=satellites_mode,
                             origenerator_ready=origenerator_ready))
    lit = {"lock": locked, "fmode": favorites_filter, "latest": bool(latest), "shuffle": latest is False}
    rows.append(tuple(
        _control(player, name, lit=lit.get(name, False),
                 dim=name == "reset" and nothing_to_reset,
                 group_break=index > 0 and _GROUP_OF[name] != _GROUP_OF[names[index - 1]])
        for index, name in enumerate(names)
    ))
    return tuple(rows)


def mode_row(player: str, *, satellites_mode: SatellitesMode,
             origenerator_ready: bool = True) -> tuple[Button, ...]:
    """The session's own row over a player: the mode pair, and minimize."""
    return (
        *(_mode_button(command, label, lit=satellites_mode is lit_mode,
                       dim=command == "origenerator_activate" and not origenerator_ready)
          for command, label, lit_mode in MODE_BUTTONS),
        _control(player, "minimize", group_break=True),
    )


def _mode_button(command: str, label: str, *, lit: bool, dim: bool) -> Button:
    return Button(command, label, STILL_STARTING_TOOLTIP if dim else MODE_TOOLTIPS[command],
                  width=FIT_THE_WORD, lit=lit, dim=dim)


def _control(player: str, name: str, *, lit: bool = False, dim: bool = False,
             group_break: bool) -> Button:
    return Button(f"{player}_{name}", CONTROL_FACES[name], CONTROL_TOOLTIPS[name],
                  lit=lit, dim=dim, favorite=name in ("lock", "fmode"),
                  danger=name == "trash", group_break=group_break)
