from __future__ import annotations

import pytest
from shared_ui.preview import Preview, window_title

from fun_time.satellites_mode import RfbSlot
from fun_time.window_roles import (
    FIXED_TOPMOST_ROLES,
    MANAGED_ROLES,
    ORIGENERATOR_ROLE,
    ORIGENERATOR_TITLE,
    RFB_SLOT_ROLES,
    is_origenerators_caption,
    role_topmost,
    visible_roles,
)


class TestRoleTopmost:
    """The windows with their own rect are always topmost.  The two that SHARE
    the browser's rect are each topmost only while they are showing something."""

    def test_every_window_with_its_own_rect_is_always_topmost(self):
        for role in FIXED_TOPMOST_ROLES:
            for rfb_slot in RfbSlot:
                assert role_topmost(role, rfb_slot) is True, (role, rfb_slot)

    def test_role_groups_partition_the_managed_set(self):
        """The Main Funestra is among the windows with a rect of their own: Kino
        and Genau both run on it, so the main slot has no hidden half to keep
        out of the band."""
        assert set(MANAGED_ROLES) == {
            "rfb", "portrait", "landscape", "main_player", "dashboard", "origenerator",
        }
        assert set(FIXED_TOPMOST_ROLES) == {"portrait", "landscape", "dashboard", "main_player"}
        assert set(RFB_SLOT_ROLES) == {"rfb", ORIGENERATOR_ROLE}
        groups = [set(FIXED_TOPMOST_ROLES), set(RFB_SLOT_ROLES)]
        assert sum(len(group) for group in groups) == len(MANAGED_ROLES)
        assert set().union(*groups) == set(MANAGED_ROLES)


class TestVisibleRoles:
    def test_the_browser_in_its_place_shows_every_window_but_the_hosted_apps(self):
        assert set(visible_roles(RfbSlot.RFB)) == {
            "portrait", "landscape", "dashboard", "main_player", "rfb"}

    def test_origenerator_in_the_browsers_place_counts_the_browser_under_it(self):
        """Covered, not parked: omniminimize takes the browser down with the rest,
        and omnirestore brings it back under the hosted window."""
        assert set(visible_roles(RfbSlot.ORIGENERATOR)) == set(MANAGED_ROLES)


class TestOrigeneratorRoles:
    """The hosted Origenerator's window joins the managed set, over the RFB's
    rect.  It is in the topmost band only while it holds the browser's place —
    and it is promoted AFTER the fixed roles, which is what stacks it above the
    window it covers."""

    def test_the_hosted_window_follows_what_holds_the_browsers_place(self):
        assert role_topmost(ORIGENERATOR_ROLE, RfbSlot.ORIGENERATOR) is True
        assert role_topmost(ORIGENERATOR_ROLE, RfbSlot.RFB) is False

    def test_the_browser_leaves_the_band_while_the_hosted_window_covers_it(self):
        """Promoting a window that is completely covered only puts it briefly
        ABOVE its cover — HWND_TOPMOST inserts at the top of the band — so every
        re-band flashed the browser over Origenerator on its way past."""
        assert role_topmost("rfb", RfbSlot.RFB) is True
        assert role_topmost("rfb", RfbSlot.ORIGENERATOR) is False

    def test_the_hosted_window_is_promoted_after_the_one_it_covers(self):
        # HWND_TOPMOST inserts at the top of the band, so a later promotion
        # wins: the hosted window must come after the fixed roles.
        assert MANAGED_ROLES.index(ORIGENERATOR_ROLE) > MANAGED_ROLES.index("rfb")


@pytest.mark.parametrize(("caption", "is_it"), [
    (ORIGENERATOR_TITLE, True),
    (window_title(ORIGENERATOR_TITLE, Preview(feature=None)), True),
    (window_title(ORIGENERATOR_TITLE, Preview(feature="a fabricated feature")), True),
    ("Origenerator Loading", False),
    ("Origenerator Portrait", False),
    ("Origenerator Landscape", False),
    ("Slideshow", False),
])
def test_the_hosted_window_is_known_by_its_caption_or_by_the_one_a_preview_wears(
        caption, is_it):
    assert is_origenerators_caption(caption) is is_it
