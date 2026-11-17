from __future__ import annotations

import logging
from dataclasses import replace
from enum import StrEnum
from functools import partial

from funestra.file_channel import append_command
from funestra.hud_title import ENHANCED_LABEL, FAVORITES_LABEL

from main_funestra.genau_verbs import genau_enhanced, genau_favorites

from .bridge_records import BridgeConfig, Handler, WindowOp
from .command_value import FUNESTRA_SOURCES
from .event_log import FAVORITE, MESSAGE, SOURCE_SYSTEM
from .fun_time_funestras import FunTimeFunestra
from .funestra_handover import shows_button_lit
from .main_list_commands import rebuild_the_main_list
from .mode_plan import kino_shows
from .shared_state import BridgeState

logger = logging.getLogger(__name__)

EVERY_FUNESTRA = (FunTimeFunestra.MAIN, FunTimeFunestra.PORTRAIT, FunTimeFunestra.LANDSCAPE)


class Collection(StrEnum):
    FAVORITES = "favorites"
    ENHANCED = "enhanced"

    @property
    def label(self) -> str:
        return {Collection.FAVORITES: FAVORITES_LABEL, Collection.ENHANCED: ENHANCED_LABEL}[self]

    @property
    def kinos_field(self) -> str:
        return f"main_{self.value}_filter"

    @property
    def genaus_field(self) -> str:
        return f"genau_{self.value}_filter"

    def told_to_genau(self, on: bool) -> str:
        return {Collection.FAVORITES: genau_favorites, Collection.ENHANCED: genau_enhanced}[self](on)


_SIDES: dict[bool | None, str] = {None: "", True: "_on", False: "_off"}


def sided_command(funestra: FunTimeFunestra, collection: Collection, target: bool | None) -> str:
    return f"{funestra.label}_{collection}{_SIDES[target]}"


def every_funestras_command(collection: Collection, target: bool | None) -> str:
    return f"{collection}{_SIDES[target] or '_toggle'}"


def _main_funestras(state: BridgeState, collection: Collection) -> bool | None:
    return getattr(state, collection.kinos_field if kino_shows(state.main_mode) else collection.genaus_field)


def _funestras(state: BridgeState, config: BridgeConfig, funestra: FunTimeFunestra,
               collection: Collection) -> bool | None:
    if funestra is FunTimeFunestra.MAIN:
        return _main_funestras(state, collection)
    return shows_button_lit(config.satellite(funestra), collection)


def every_funestra_keeps_only(state: BridgeState, config: BridgeConfig, collection: Collection,
                              funestras: tuple[FunTimeFunestra, ...] = EVERY_FUNESTRA) -> bool:
    return all(kept for funestra in funestras
               if (kept := _funestras(state, config, funestra, collection)) is not None)


def _tell_the_main_funestra(state: BridgeState, config: BridgeConfig, collection: Collection,
                            enabled: bool) -> BridgeState:
    if kino_shows(state.main_mode):
        state = replace(state, **{collection.kinos_field: enabled})
        rebuild_the_main_list(state, config)
        return state
    append_command(config.main_funestra.cmd_file, collection.told_to_genau(enabled))
    return replace(state, **{collection.genaus_field: enabled})


def _dispatch(collection: Collection, asked_for: tuple[FunTimeFunestra, ...], target: bool | None,
              state: BridgeState, config: BridgeConfig) -> tuple[BridgeState, list[WindowOp]]:
    enabled = (not every_funestra_keeps_only(state, config, collection, asked_for)
               if target is None else target)
    for funestra in asked_for:
        now = _funestras(state, config, funestra, collection)
        if now in (None, enabled):
            continue
        if funestra is FunTimeFunestra.MAIN:
            state = _tell_the_main_funestra(state, config, collection, enabled)
        else:
            append_command(config.origenerator.cmd_file,
                           sided_command(funestra, collection, enabled))
    logger.info("%s %s: %s", collection.label, "on" if enabled else "off",
                ", ".join(funestra.label for funestra in asked_for))
    source = FUNESTRA_SOURCES[asked_for[0]] if len(asked_for) == 1 else SOURCE_SYSTEM
    return state, [WindowOp(op="message", key=f"{collection.label} {'on' if enabled else 'off'}",
                            source=source,
                            level=FAVORITE if enabled and collection is Collection.FAVORITES
                            else MESSAGE)]


def _handle(collection: Collection, funestras: tuple[FunTimeFunestra, ...], target: bool | None,
            state: BridgeState, config: BridgeConfig,
            _target_path: str) -> tuple[BridgeState, list[WindowOp]]:
    return _dispatch(collection, funestras, target, state, config)


HANDLERS: dict[str, Handler] = {
    **{every_funestras_command(collection, target): partial(_handle, collection, EVERY_FUNESTRA,
                                                            target)
       for collection in Collection for target in _SIDES
       if target is not None or collection is Collection.FAVORITES},
    **{sided_command(FunTimeFunestra.MAIN, collection, target): partial(
        _handle, collection, (FunTimeFunestra.MAIN,), target)
       for collection in Collection for target in _SIDES},
}
