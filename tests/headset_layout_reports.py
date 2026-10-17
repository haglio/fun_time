from __future__ import annotations

from pathlib import Path

from fun_time_vr.layout import Layout, layout_from_json


def reported_layout(path: Path) -> Layout:
    return layout_from_json(path.read_text(encoding="utf-8")) if path.exists() else Layout()
