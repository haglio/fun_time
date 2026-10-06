from __future__ import annotations

from pathlib import Path

from app_support.file_channel import publish_whole

READER_HOLD_BUDGET_S = 1.0
_RETRY_SPACING_S = 0.005


def replace_despite_readers(path: Path, text: str) -> bool:
    return publish_whole(path, text,
                         attempts=round(READER_HOLD_BUDGET_S / _RETRY_SPACING_S),
                         delay_s=_RETRY_SPACING_S)
