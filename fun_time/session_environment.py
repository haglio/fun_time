from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionEnvironment:
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
