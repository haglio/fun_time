from __future__ import annotations

import logging

from funestra.file_channel import append_command
from funestra.funestra_verbs import LOCK_ON, play_file

from .bridge_records import BridgeConfig
from .fun_time_funestras import FunTimeFunestra
from .modes import same_file_to_windows, scripted_item
from .satellite_control import read_satellite_status

logger = logging.getLogger(__name__)


def send_satellite(config: BridgeConfig, funestra: FunTimeFunestra, verb: str) -> None:
    append_command(config.satellite(funestra).cmd_file, verb)


def lock_on_what_was_meant(funestra: FunTimeFunestra, config: BridgeConfig,
                           aimed_at: str = "") -> str:
    on_screen = read_satellite_status(config.satellite(funestra).status_file).video
    item = aimed_at or on_screen
    if not same_file_to_windows(item, on_screen):
        send_satellite(config, funestra, play_file(scripted_item(item, config.regen_metadata_root)))
        logger.info("Lock moved Funestra %d onto %s", funestra, item)
    send_satellite(config, funestra, LOCK_ON)
    logger.info("Locked %s satellite", FunTimeFunestra(funestra).label)
    return item

