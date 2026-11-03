from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

PROJECT_DIR = Path(__file__).resolve().parent.parent

ICONS = sorted(PROJECT_DIR.glob("*.ico"))


def test_there_are_icons_to_check():
    assert ICONS, f"no .ico files found in {PROJECT_DIR}"


@pytest.mark.parametrize("icon", ICONS, ids=lambda p: p.name)
def test_every_frame_keeps_its_shape_without_the_alpha_channel(icon: Path):
    with Image.open(icon) as img:
        sizes = sorted(img.ico.sizes())
        assert sizes, f"{icon.name} has no frames"
        for size in sizes:
            frame = img.ico.getimage(size).convert("RGBA")
            flattened = {(r, g, b) for r, g, b, _ in frame.get_flattened_data()}
            assert len(flattened) > 1, (
                f"{icon.name} at {size[0]}x{size[1]} is one flat color once alpha is "
                "discarded — its mark lives only in transparency, so anything that "
                "drops the channel draws a solid square"
            )


@pytest.mark.parametrize("icon", ICONS, ids=lambda p: p.name)
def test_transparent_pixels_carry_the_background_they_sit_on(icon: Path):
    with Image.open(icon) as img:
        for size in sorted(img.ico.sizes()):
            frame = img.ico.getimage(size).convert("RGBA")
            under = {(r, g, b) for r, g, b, a in frame.get_flattened_data() if a == 0}
            assert under <= {(0, 0, 0)}, (
                f"{icon.name} at {size[0]}x{size[1]} paints its transparent pixels "
                f"{sorted(under)[:3]} instead of black"
            )

