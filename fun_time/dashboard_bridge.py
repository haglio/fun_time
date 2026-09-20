from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DashboardSnapshot:
    """The five facts the bar draws, written every tick and read back by the panel."""

    omni_paused: bool = False
    voice_active: bool = True
    # The room's F-mode: every player narrowed at once.  Each player carries its
    # own switch on its own HUD, so the bar's one lights only when all three are
    # on — which is the only state a single button can honestly claim.
    f_mode: bool = False
    # Whether this session is the headset's.  The bar's last control is the way
    # across to the other one, and which way that is depends on where you are.
    in_vr: bool = False
    nothing_to_reset: bool = False


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
    )


# utf-16 is what the writer emits; the other two are what a reader has always
# also accepted, and older sessions' files are still read back.
SNAPSHOT_ENCODINGS = ("utf-8-sig", "utf-16", "utf-8")


def decode_snapshot(raw: bytes) -> str:
    """The snapshot's text — beside the writer, which decides the encoding.

    Newlines are normalized here, in the decoder every reader shares: the writer
    opens in text mode, so on Windows its ``\n`` reaches disk as ``\r\n``.
    """
    for encoding in SNAPSHOT_ENCODINGS:
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        return text.replace("\r\n", "\n").replace("\r", "\n")
    raise UnicodeDecodeError(
        "dashboard_state", raw, 0, 1, "unable to decode dashboard snapshot")


def _read_existing_snapshot(path: Path) -> str:
    """What is on disk, or "" — this side never fails over a read."""
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
