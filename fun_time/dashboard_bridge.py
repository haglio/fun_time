from __future__ import annotations

from pathlib import Path


def build_dashboard_snapshot_text(
    *,
    f_mode_enabled: bool,
    osr2_mode: str,
    primary_mode: str,
    portrait_locked: bool,
    landscape_locked: bool,
    omni_paused: bool = False,
    voice_active: bool = True,
) -> str:
    return (
        "[fmode]\n"
        f"enabled={'1' if f_mode_enabled else '0'}\n"
        "[osr2]\n"
        f"mode={osr2_mode}\n"
        "[omnipause]\n"
        f"active={'1' if omni_paused else '0'}\n"
        "[voice]\n"
        f"active={'1' if voice_active else '0'}\n"
        "[primary]\n"
        f"mode={primary_mode}\n"
        "locked=0\n"
        "[portrait]\n"
        f"locked={'1' if portrait_locked else '0'}\n"
        "[landscape]\n"
        f"locked={'1' if landscape_locked else '0'}\n"
    )


def _read_existing_snapshot(path: Path) -> str:
    for encoding in ("utf-16", "utf-8"):
        try:
            return path.read_text(encoding=encoding)
        except FileNotFoundError:
            return ""
        except UnicodeError:
            continue
        except OSError:
            return ""
    return ""


def write_dashboard_snapshot(
    output_file: str | Path,
    *,
    f_mode_enabled: bool,
    osr2_mode: str,
    primary_mode: str,
    portrait_locked: bool,
    landscape_locked: bool,
    omni_paused: bool = False,
    voice_active: bool = True,
) -> bool:
    path = Path(output_file)
    text = build_dashboard_snapshot_text(
        f_mode_enabled=f_mode_enabled,
        osr2_mode=osr2_mode,
        primary_mode=primary_mode,
        portrait_locked=portrait_locked,
        landscape_locked=landscape_locked,
        omni_paused=omni_paused,
        voice_active=voice_active,
    )
    if _read_existing_snapshot(path) == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-16")
    return True
