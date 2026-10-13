from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CONTRACT_FILE = "origenerator_contract.json"


def published_by(checkout: str | Path) -> dict | None:
    document = Path(checkout) / CONTRACT_FILE
    if not document.is_file():
        return None
    return json.loads(document.read_text(encoding="utf-8"))


def declared_flags(contract: dict) -> frozenset[str]:
    by_side = (flag for group in ("region_flags", "player_flags")
               for flags in contract[group].values() for flag in flags)
    return frozenset({*contract["required_flags"], *by_side, *contract.get("headset_flags", ())})


def _each_flag_with_its_values(args: list[str]) -> list[list[str]]:
    flags: list[list[str]] = []
    for word in args:
        if word.startswith("--") or not flags:
            flags.append([word])
        else:
            flags[-1].append(word)
    return flags


def the_args_it_declares(checkout: str | Path, args: list[str]) -> list[str]:
    contract = published_by(checkout)
    if contract is None:
        return args
    declared = declared_flags(contract)
    flags = _each_flag_with_its_values(args)
    if left_off := [flag for flag, *_values in flags if flag not in declared]:
        logger.warning("The Origenerator at %s does not declare %s, so it is sent its session "
                       "without them", checkout, ", ".join(left_off))
    return [word for flag in flags if flag[0] in declared for word in flag]
