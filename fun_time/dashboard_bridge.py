from __future__ import annotations

from pathlib import Path


def build_dashboard_snapshot_text(
    *,
    omni_paused: bool = False,
    voice_active: bool = True,
) -> str:
    return (
        "[omnipause]\n"
        f"active={'1' if omni_paused else '0'}\n"
        "[voice]\n"
        f"active={'1' if voice_active else '0'}\n"
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
    omni_paused: bool = False,
    voice_active: bool = True,
) -> bool:
    path = Path(output_file)
    text = build_dashboard_snapshot_text(
        omni_paused=omni_paused,
        voice_active=voice_active,
    )
    if _read_existing_snapshot(path) == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-16")
    return True
