from __future__ import annotations

from fun_time.window_roles import (
    FIXED_TOPMOST_ROLES,
    MAIN_SLOT_ROLES,
    MANAGED_ROLES,
    ORIGENERATOR_ROLE,
    role_topmost,
    visible_main_slot_roles,
)


class TestRoleTopmost:
    """The windows with their own rect are always topmost.  The two that SHARE
    the main player's rect are each topmost only while they are showing something —
    in video mode that is both, with Genau's HUD stacked above the main player by promotion
    order, which is not this flag's job."""

    def test_main_player_is_topmost_whenever_it_displays(self):
        # The main player owns the display in video mode, so it floats topmost there.
        assert role_topmost("main_player", "video") is True
        # In genau mode the main player is hidden and stays out of the band.
        assert role_topmost("main_player", "genau") is False

    def test_genau_is_topmost_in_both_modes(self):
        """Genau is promoted last, so being in the band at all puts it ABOVE
        The main player — the display in genau mode, the HUD layer over the video in video
        mode."""
        assert role_topmost("genau", "genau") is True
        assert role_topmost("genau", "video") is True

    def test_every_window_with_its_own_rect_is_always_topmost(self):
        for role in FIXED_TOPMOST_ROLES:
            for mode in ("video", "genau"):
                assert role_topmost(role, mode) is True, (role, mode)

    def test_visible_main_slot_roles_names_the_players_on_that_rect(self):
        """What anything acting on "the main player's window" has to reach: the
        mode's own player, both in video mode where Genau's HUD sits over the main player's
        video, and never the slot-mate the mode has parked — minimizing a hidden
        window is what drags it back into view."""
        assert visible_main_slot_roles("genau") == ("genau",)
        assert visible_main_slot_roles("video") == ("main_player", "genau")

    def test_visible_main_slot_roles_agrees_with_the_band_policy(self):
        """Derived from role_topmost rather than listed again, so the two answers
        cannot drift: a main-slot player is in the band exactly when it shows."""
        for mode in ("video", "genau"):
            assert visible_main_slot_roles(mode) == tuple(
                role for role in MAIN_SLOT_ROLES if role_topmost(role, mode)), mode

    def test_role_groups_partition_the_managed_set(self):
        assert set(MANAGED_ROLES) == {
            "rfb", "portrait", "landscape", "genau", "main_player", "dashboard",
            "origenerator",
        }
        assert set(FIXED_TOPMOST_ROLES) == {"rfb", "portrait", "landscape", "dashboard"}
        assert set(MAIN_SLOT_ROLES) == {"main_player", "genau"}
        # The three groups are disjoint and together cover every managed role.
        groups = [set(FIXED_TOPMOST_ROLES), {ORIGENERATOR_ROLE}, set(MAIN_SLOT_ROLES)]
        assert sum(len(group) for group in groups) == len(MANAGED_ROLES)
        assert set().union(*groups) == set(MANAGED_ROLES)


class TestOrigeneratorRoles:
    """The hosted Origenerator's window joins the managed set, over the RFB's
    rect.  It is in the topmost band only while the satellites are in
    origenerator mode — and it is promoted AFTER the fixed roles, which is what
    stacks it above the window it covers."""

    def test_the_hosted_window_follows_the_satellites_mode(self):
        for main_mode in ("video", "genau"):
            assert role_topmost(ORIGENERATOR_ROLE, main_mode, "origenerator") is True
            assert role_topmost(ORIGENERATOR_ROLE, main_mode, "video") is False

    def test_the_browser_leaves_the_band_while_the_hosted_window_covers_it(self):
        """The RFB shares its rect with the hosted app's main window, so it is
        mode-dependent the same way the main-slot pair is.  Promoting a window
        that is completely covered only puts it briefly ABOVE its cover —
        HWND_TOPMOST inserts at the top of the band — so every re-band flashed
        the browser over Origenerator on its way past."""
        assert role_topmost("rfb", "video", "video") is True
        assert role_topmost("rfb", "video", "origenerator") is False

    def test_the_roles_with_their_own_rects_ignore_the_satellites_mode(self):
        for satellites_mode in ("video", "origenerator"):
            assert role_topmost("portrait", "video", satellites_mode) is True
            assert role_topmost("dashboard", "video", satellites_mode) is True
            assert role_topmost("main_player", "genau", satellites_mode) is False

    def test_the_hosted_window_is_promoted_after_the_one_it_covers(self):
        # HWND_TOPMOST inserts at the top of the band, so a later promotion
        # wins: the hosted window must come after the fixed roles.
        assert MANAGED_ROLES.index(ORIGENERATOR_ROLE) > MANAGED_ROLES.index("rfb")

