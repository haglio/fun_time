"""What the broker says about its own processes and source.

This session manages it without importing it, so those names were copied here
by hand and a rename over there broke every reading of them silently.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app_support.process_identity import ProcessNamer

logger = logging.getLogger(__name__)

#: Beside the launcher, which is the one path this session is configured with.
CONTRACT_FILE = "broker_contract.json"


@dataclass(frozen=True)
class BrokerContract:
    app_name: str
    package_dir: str
    broker_module: str
    tray_module: str
    tray_launcher: str

    @property
    def image_pattern(self) -> str:
        # The bare interpreters too: naming a process is best-effort.
        return ProcessNamer(self.app_name).process_name_pattern

    @property
    def command_line_pattern(self) -> str:
        return _as_pattern(self.broker_module) + "|" + _as_pattern(self.tray_module)

    @property
    def broker_command_line_pattern(self) -> str:
        # The broker alone: a live tray is not a live broker.
        return _as_pattern(self.broker_module)

    @property
    def launcher_pattern(self) -> str:
        return _as_pattern(self.tray_launcher)


def _as_pattern(literal: str) -> str:
    return literal.replace(".", "\\.")


def read(tray_launcher: Path | str | None) -> BrokerContract | None:
    """The document beside *tray_launcher*, or None with a line in the log --
    a broker this session can launch but cannot date or find."""
    if tray_launcher is None:
        return None
    published = Path(tray_launcher).parent / CONTRACT_FILE
    try:
        named = json.loads(published.read_text(encoding="utf-8"))
        return BrokerContract(
            app_name=named["app_name"],
            package_dir=named["package_dir"],
            broker_module=named["broker_module"],
            tray_module=named["tray_module"],
            tray_launcher=named["tray_launcher"],
        )
    except (OSError, ValueError, KeyError, TypeError) as refusal:
        logger.error("No broker contract this session can read at %s (%s); it "
                     "cannot date or find that broker's processes",
                     published, refusal)
        return None
