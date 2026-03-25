from __future__ import annotations

from pathlib import Path


def build_dashboard_snapshot_text(
    *,
    f_mode_enabled: bool,
    robot_link_enabled: bool,
    osr2_mode: str,
    mfp_alive: bool,
    primary_uses_robot_hand: bool,
    portrait_locked: bool,
    landscape_locked: bool,
) -> str:
    return (
        "[fmode]\n"
        f"enabled={'1' if f_mode_enabled else '0'}\n"
        "[robot_link]\n"
        f"enabled={'1' if robot_link_enabled else '0'}\n"
        "[osr2]\n"
        f"mode={osr2_mode}\n"
        "[mfp]\n"
        f"alive={'1' if mfp_alive else '0'}\n"
        "[primary]\n"
        f"uses_robot_hand={'1' if primary_uses_robot_hand else '0'}\n"
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
    robot_link_enabled: bool,
    osr2_mode: str,
    mfp_alive: bool,
    primary_uses_robot_hand: bool,
    portrait_locked: bool,
    landscape_locked: bool,
) -> bool:
    path = Path(output_file)
    text = build_dashboard_snapshot_text(
        f_mode_enabled=f_mode_enabled,
        robot_link_enabled=robot_link_enabled,
        osr2_mode=osr2_mode,
        mfp_alive=mfp_alive,
        primary_uses_robot_hand=primary_uses_robot_hand,
        portrait_locked=portrait_locked,
        landscape_locked=landscape_locked,
    )
    if _read_existing_snapshot(path) == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-16")
    return True
