from __future__ import annotations

from typing import TYPE_CHECKING

from .command_value import SAY, Command, Scope
from .fun_time_funestras import FunTimeFunestra
from .hosted_contract import said_to_core, said_to_the_hosted_app
from .origenerator_switches import RfbMode, hosts

if TYPE_CHECKING:
    from .bridge_records import BridgeConfig
    from .shared_state import BridgeState


def line_said_bare_to_the_hosted_app(command: Command, state: BridgeState,
                                     config: BridgeConfig) -> str | None:
    if command.said_to is not Scope.ACTIVE:
        return None
    said = command.aimed_at_the_active(FunTimeFunestra.PORTRAIT)
    if said.action != SAY:
        return None
    holding = next((funestra for funestra in FunTimeFunestra.SATELLITES
                    if hosts(funestra, state, config)), None)
    if holding is not None:
        return said_to_the_hosted_app(holding.label, said.argument)
    shown = (config.hosts_origenerator and state.origenerator_ready
             and state.rfb_mode is RfbMode.ORIGENERATOR)
    return said_to_core(said.argument) if shown else None
