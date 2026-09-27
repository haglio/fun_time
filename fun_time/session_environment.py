"""What kind of session this is, read once at the process edge.

Two of the five ``FUN_TIME_*`` switches are missing here on purpose: they are
read by children that inherit the environment -- ``FUN_TIME_MUTE_AUDIO`` in the
satellite and the audio companion, ``FUN_TIME_FAKE_MONITORS`` in
:func:`fun_time.monitors.enumerate_monitors`, which the dashboard calls in its
own process -- so those stay environment reads and must still reach children.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionEnvironment:
    """The switches a session was launched under, as values rather than ambience."""

    integration: bool = False
    show_overlays: bool = True
    dashboard_enabled: bool = True

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> SessionEnvironment:
        integration = environ.get("FUN_TIME_RUN_INTEGRATION") == "1"
        return cls(
            integration=integration,
            show_overlays=(not integration
                           or environ.get("FUN_TIME_INTEGRATION_OVERLAYS") == "1"),
            dashboard_enabled=environ.get("FUN_TIME_DISABLE_DASHBOARD") != "1",
        )


ORDINARY_SESSION = SessionEnvironment()
