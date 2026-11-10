from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .rfb_modes import RfbMode


@dataclass(frozen=True)
class DashboardSnapshot:
    omni_paused: bool = False
    voice_active: bool = True
    f_mode: bool = False
    in_vr: bool = False
    nothing_to_reset: bool = False
    headset_ready: bool = True
    origenerator_ready: bool = False
    rfb_mode: RfbMode = RfbMode.RFB


def build_dashboard_snapshot_text(snapshot: DashboardSnapshot | None = None) -> str:
    snapshot = snapshot or DashboardSnapshot()
    return (
        "[omnipause]\n"
        f"active={'1' if snapshot.omni_paused else '0'}\n"
        "[voice]\n"
        f"active={'1' if snapshot.voice_active else '0'}\n"
        "[fmode]\n"
        f"active={'1' if snapshot.f_mode else '0'}\n"
        "[reset]\n"
        f"nothing={'1' if snapshot.nothing_to_reset else '0'}\n"
        "[session]\n"
        f"vr={'1' if snapshot.in_vr else '0'}\n"
        "[headset]\n"
        f"ready={'1' if snapshot.headset_ready else '0'}\n"
        "[origenerator]\n"
        f"ready={'1' if snapshot.origenerator_ready else '0'}\n"
        f"in_the_rfb_mode={'1' if snapshot.rfb_mode is RfbMode.ORIGENERATOR else '0'}\n"
    )


SNAPSHOT_ENCODINGS = ("utf-8-sig", "utf-16", "utf-8")


def decode_snapshot(raw: bytes) -> str:
    # The writer opens in text mode, so on Windows its newline reaches disk as a carriage return and
    # a newline.
    for encoding in SNAPSHOT_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        return text.replace("\r\n", "\n").replace("\r", "\n")
    raise UnicodeDecodeError(
        "dashboard_state", raw, 0, 1, "unable to decode dashboard snapshot")


def _read_existing_snapshot(path: Path) -> str:
    try:
        return decode_snapshot(path.read_bytes())
    except (OSError, UnicodeDecodeError):
        return ""


def write_dashboard_snapshot(output_file: str | Path, snapshot: DashboardSnapshot) -> bool:
    path = Path(output_file)
    text = build_dashboard_snapshot_text(snapshot)
    if _read_existing_snapshot(path) == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-16")
    return True
