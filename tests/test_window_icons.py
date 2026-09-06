"""Every window icon has to survive having its alpha discarded.

Nau's icon once came out of Task Manager as a solid pink square.  The mark was
there — but only in the alpha channel: its 256x256 frame was pink edge to edge,
with the N cut out by transparency alone.  Anything that flattens the image
before drawing it gets the pink rectangle and nothing else, and that is a whole
class of consumer (the task list, small-icon paths, thumbnail extractors), none
of which this repo controls.

The family's icons are drawn the safe way — the mark on black, with the alpha
agreeing — and this holds every icon in this repo to it, each frame of each,
because the loaders read a .ico's LARGEST frame and the one that went wrong was
the only frame authored the other way.  The gate lived in the genau repo while
that repo shipped an icon; it ships none now, and this one ships five.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

PROJECT_DIR = Path(__file__).resolve().parent.parent

ICONS = sorted(PROJECT_DIR.glob("*.ico"))


def test_there_are_icons_to_check():
    """A glob that quietly matches nothing would make every test below pass."""
    assert ICONS, f"no .ico files found in {PROJECT_DIR}"


@pytest.mark.parametrize("icon", ICONS, ids=lambda p: p.name)
def test_every_frame_keeps_its_shape_without_the_alpha_channel(icon: Path):
    """Each frame, not just the one a given loader happens to pick."""
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
    """Fully transparent pixels are black, as the whole family draws them.

    This is what makes the frame above safe rather than merely lucky: a mark
    painted onto black keeps its edges when the alpha goes, while one painted
    onto its own color disappears into it.
    """
    with Image.open(icon) as img:
        for size in sorted(img.ico.sizes()):
            frame = img.ico.getimage(size).convert("RGBA")
            under = {(r, g, b) for r, g, b, a in frame.get_flattened_data() if a == 0}
            assert under <= {(0, 0, 0)}, (
                f"{icon.name} at {size[0]}x{size[1]} paints its transparent pixels "
                f"{sorted(under)[:3]} instead of black"
            )

