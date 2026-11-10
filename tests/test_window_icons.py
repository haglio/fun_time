from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

PROJECT_DIR = Path(__file__).resolve().parent.parent

ICONS = sorted(PROJECT_DIR.glob("*.ico"))

THE_BLACK_A_FLATTENED_MARK_KEEPS_ITS_EDGES_ON = (0, 0, 0)


def _every_frame_a_loader_might_pick(icon: Path):
    with Image.open(icon) as img:
        sizes = sorted(img.ico.sizes())
        assert sizes, f"{icon.name} has no frames"
        for size in sizes:
            yield size, img.ico.getimage(size).convert("RGBA")


def _colors_task_manager_draws_having_dropped_the_alpha(frame) -> set[tuple[int, int, int]]:
    return {(r, g, b) for r, g, b, _ in frame.get_flattened_data()}


def test_there_are_icons_to_check():
    assert ICONS, f"no .ico files found in {PROJECT_DIR}"


@pytest.mark.parametrize("icon", ICONS, ids=lambda p: p.name)
def test_every_frame_keeps_its_shape_without_the_alpha_channel(icon: Path):
    for size, frame in _every_frame_a_loader_might_pick(icon):
        flattened = _colors_task_manager_draws_having_dropped_the_alpha(frame)
        assert len(flattened) > 1, (
            f"{icon.name} at {size[0]}x{size[1]} is one flat color once alpha is "
            "discarded — its mark lives only in transparency, so anything that "
            "drops the channel draws a solid square, and a loader reads the "
            "largest frame, so no frame may be the one authored that way"
        )


@pytest.mark.parametrize("icon", ICONS, ids=lambda p: p.name)
def test_fully_transparent_pixels_are_the_black_a_flattened_mark_stands_on(icon: Path):
    for size, frame in _every_frame_a_loader_might_pick(icon):
        under = {(r, g, b) for r, g, b, a in frame.get_flattened_data() if a == 0}
        assert under <= {THE_BLACK_A_FLATTENED_MARK_KEEPS_ITS_EDGES_ON}, (
            f"{icon.name} at {size[0]}x{size[1]} paints its transparent pixels "
            f"{sorted(under)[:3]} instead of black"
        )

