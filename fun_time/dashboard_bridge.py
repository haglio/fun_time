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


# utf-16 is what the writer emits; the other two are what a reader has always
# also accepted, and older sessions' files are still read back.
SNAPSHOT_ENCODINGS = ("utf-8-sig", "utf-16", "utf-8")


def decode_snapshot(raw: bytes) -> str:
    """The snapshot's text — beside the writer, which decides the encoding."""
    for encoding in SNAPSHOT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError(
        "dashboard_state", raw, 0, 1, "unable to decode dashboard snapshot")


def _read_existing_snapshot(path: Path) -> str:
    """What is on disk, or "" — this side never fails over a read.

    Newlines normalized: `write_text` opens in text mode, so on Windows a
    ``\n`` lands as ``\r\n`` and a raw decode never equals what we will write.
    """
    try:
        raw = decode_snapshot(path.read_bytes())
    except (OSError, UnicodeDecodeError):
        return ""
    return raw.replace("\r\n", "\n").replace("\r", "\n")


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
