from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from funestra.hud_placement import HudCorner

from .event_log import SOURCE_LANDSCAPE, SOURCE_MAIN, SOURCE_PORTRAIT, SOURCE_SYSTEM
from .fun_time_funestras import FunTimeFunestra
from .mode_plan import EnhancementLevel


class Scope(StrEnum):
    SESSION = ""
    MAIN = "main"
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"
    SATELLITES = "satellites"
    ALL = "all"
    ACTIVE = "active"

    @property
    def funestras(self) -> tuple[Scope, ...]:
        return _GROUPS.get(self, (self,))


_GROUPS: dict[Scope, tuple[Scope, ...]] = {
    Scope.SATELLITES: (Scope.PORTRAIT, Scope.LANDSCAPE),
    Scope.ALL: (Scope.MAIN, Scope.PORTRAIT, Scope.LANDSCAPE),
}

_SATELLITE_SCOPES: dict[Scope, FunTimeFunestra] = {
    Scope.PORTRAIT: FunTimeFunestra.PORTRAIT,
    Scope.LANDSCAPE: FunTimeFunestra.LANDSCAPE,
}

_SCOPE_WORDS = (Scope.PORTRAIT, Scope.LANDSCAPE, Scope.SATELLITES, Scope.ALL, Scope.ACTIVE)

_THE_MAIN_FUNESTRAS_OWN = ("main_", "genau_")


FILTER = "filter"
SAY = "say"
_SAY = f"{SAY}_"
_VALUE_BAR = "|"

MAIN_EQUIVALENTS = MappingProxyType({
    "next": "main_next",
    "prev": "main_prev",
    "nudge_next": "main_nudge_next",
    "nudge_prev": "main_nudge_prev",
    "funscript_jump": "main_funestra_funscript_jump",
    "next_funscripted": "main_funestra_next_funscripted",
    "no_loop": "main_funestra_loop_cancel",
    "lock_on": "main_lock_on",
    "lock_off": "main_lock_off",
    "fmode": "main_fmode",
    "fmode_on": "main_fmode_on",
    "fmode_off": "main_fmode_off",
    "reset": "main_reset",
    "trash": "genau_trash_flick",
    "latest": "main_latest",
    "shuffle": "main_shuffle",
    "hud_minimize": "main_hud_minimize",
    "hud_restore": "main_hud_restore",
    **{f"hud_{corner}": f"main_hud_{corner}" for corner in HudCorner},
    **{f"enhancement_{level}": f"main_enhancement_{level}" for level in EnhancementLevel},
    "mute": "audio_mute",
    "unmute": "audio_unmute",
    "volume_down": "audio_volume_down",
    "volume_up": "audio_volume_up",
})

FUNESTRA_SOURCES = MappingProxyType({
    FunTimeFunestra.MAIN: SOURCE_MAIN,
    FunTimeFunestra.PORTRAIT: SOURCE_PORTRAIT,
    FunTimeFunestra.LANDSCAPE: SOURCE_LANDSCAPE,
})


@dataclass(frozen=True)
class Command:
    said_to: Scope
    action: str
    argument: str = ""
    text: str = ""

    @classmethod
    def to(cls, funestra: FunTimeFunestra, action: str) -> Command:
        return parse_command(f"{FunTimeFunestra(funestra).label}_{action}")

    @property
    def key(self) -> tuple[Scope, str]:
        if self.action == SAY:
            return self.said_to, f"{SAY} {self.argument}"
        return self.said_to, self.action

    @property
    def satellite(self) -> FunTimeFunestra | None:
        return _SATELLITE_SCOPES.get(self.said_to)

    @property
    def funestra(self) -> FunTimeFunestra | None:
        if self.action == FILTER:
            return None
        if self.said_to is Scope.SESSION and self.action.startswith(_THE_MAIN_FUNESTRAS_OWN):
            return FunTimeFunestra.MAIN
        return self.satellite

    def message_source(self, active_funestra: FunTimeFunestra | None) -> str:
        funestra = active_funestra if self.said_to is Scope.ACTIVE else self.funestra
        return FUNESTRA_SOURCES.get(funestra, SOURCE_SYSTEM)

    def aimed_at_the_active(self, active_funestra: FunTimeFunestra) -> Command:
        if self.said_to is not Scope.ACTIVE:
            return self
        if active_funestra != FunTimeFunestra.MAIN:
            return self._aimed_at(Scope(FunTimeFunestra(active_funestra).label))
        on_the_main = self._on_the_main_funestra()
        return self if on_the_main is None else parse_command(on_the_main)

    def _on_the_main_funestra(self) -> str | None:
        return main_funestras_own(self._rest())

    def each(self) -> tuple[Command, ...]:
        if self.action == FILTER or self.said_to not in _GROUPS:
            return (self,)
        return tuple(self._aimed_at(scope) for scope in self.said_to.funestras)

    def halves(self) -> tuple[Command, ...]:
        if self.action != FILTER or self.said_to not in _GROUPS:
            return (self,)
        return tuple(self._aimed_at(scope) for scope in self.said_to.funestras)

    def _aimed_at(self, scope: Scope) -> Command:
        if self.action == FILTER:
            return parse_command(set_command(scope, self.argument))
        return parse_command(f"{scope}_{self._rest()}")

    def _rest(self) -> str:
        return self.text.removeprefix(f"{self.said_to}_")


def main_funestras_own(action: str) -> str | None:
    if action.startswith("speed_"):
        return f"main_funestra_{action}"
    return MAIN_EQUIVALENTS.get(action)


def set_command(scope: str, query: str) -> str:
    return f"{FILTER}_{scope}_{_slug(query)}"


def say_command(satellite: str, phrase: str) -> str:
    return f"{satellite}_{_SAY}{_slug(phrase)}"


def _slug(query: str) -> str:
    return query.replace(" ", "_")


def _unslug(slug: str) -> str:
    return slug.replace("_", " ")


def parse_command(text: str) -> Command:
    if text.startswith(f"{FILTER}_"):
        filtered = _parse_filter(text)
        if filtered is not None:
            return filtered
    said_to, rest = _scope_of(text)
    words = rest.removeprefix(_SAY)
    if words and words != rest and (said_to in _SATELLITE_SCOPES or said_to is Scope.ACTIVE):
        return Command(said_to, SAY, _unslug(words), text)
    action, bar, value = rest.partition(_VALUE_BAR)
    if bar:
        return Command(said_to, action, value, text)
    action, _, number = rest.rpartition("_")
    if action and number.removeprefix("-").isdigit():
        return Command(said_to, action, number, text)
    return Command(said_to, rest, "", text)


def _parse_filter(text: str) -> Command | None:
    rest = text.removeprefix(f"{FILTER}_")
    for scope in (Scope.SATELLITES, Scope.PORTRAIT, Scope.LANDSCAPE):
        if rest.startswith(f"{scope}_"):
            return Command(scope, FILTER, _unslug(rest.removeprefix(f"{scope}_")), text)
    return None


def _scope_of(text: str) -> tuple[Scope, str]:
    for scope in _SCOPE_WORDS:
        if text.startswith(f"{scope}_"):
            return scope, text.removeprefix(f"{scope}_")
    return Scope.SESSION, text
