"""Asking the machine, through WMI, which processes match a command line.

Bounded, because WMI under load does not always answer, and never raising:
every caller's answer to no answer is to sweep nothing.
"""
from __future__ import annotations

import logging
import subprocess

from .orchestrator_broker import subprocess_window_kwargs

logger = logging.getLogger(__name__)

SWEEP_BUDGET_S = 60.0


def sweep_processes(powershell_command: str, *, read_output: bool = False,
                    budget_s: float = SWEEP_BUDGET_S) -> str:
    """Run *powershell_command*; "" unless asked to read what it printed."""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden",
             "-Command", powershell_command],
            check=False, timeout=budget_s,
            **({"capture_output": True, "text": True} if read_output else {}),
            **subprocess_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        logger.warning("A process sweep did not answer within %ds and was given up on: %s",
                       int(budget_s), powershell_command)
        return ""
    return result.stdout if read_output and result.stdout else ""
