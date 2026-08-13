from __future__ import annotations

from fun_time.window_roles import (
    FIXED_TOPMOST_ROLES,
    MANAGED_ROLES,
    PRIMARY_SLOT_ROLES,
    role_topmost,
    visible_main_slot_roles,
)


class TestRoleTopmost:
    """The windows with their own rect are always topmost.  The two that SHARE
    the main player's rect are each topmost only while they are showing something —
    in hybrid that is both, with Genau's HUD stacked above Nau by promotion
    order, which is not this flag's job."""

    def test_nau_is_topmost_whenever_it_displays(self):
        # Nau owns the display in nau and hybrid, so it floats topmost in both.
        assert role_topmost("nau", "nau") is True
        assert role_topmost("nau", "hybrid") is True
        # In genau mode Nau is hidden and stays out of the band.
        assert role_topmost("nau", "genau") is False

    def test_genau_is_topmost_only_where_it_displays(self):
        """Genau is promoted last, so being in the band at all puts it ABOVE
        Nau.  In nau mode it is the hidden slot-mate and must stay out — leaving
        omnipause re-applies the bands with no hide op to mask it, and Genau came
        back over Nau's video."""
        assert role_topmost("genau", "genau") is True
        assert role_topmost("genau", "hybrid") is True
        assert role_topmost("genau", "nau") is False

    def test_every_window_with_its_own_rect_is_always_topmost(self):
        for role in FIXED_TOPMOST_ROLES:
            for mode in ("nau", "hybrid", "genau"):
                assert role_topmost(role, mode) is True, (role, mode)

    def test_visible_main_slot_roles_names_the_players_on_that_rect(self):
        """What anything acting on "the main player's window" has to reach: the
        mode's own player, both in hybrid where Genau's HUD sits over Nau's video,
        and never the slot-mate the mode has parked — minimizing a hidden window
        is what drags it back into view."""
        assert visible_main_slot_roles("nau") == ("nau",)
        assert visible_main_slot_roles("genau") == ("genau",)
        assert visible_main_slot_roles("hybrid") == ("nau", "genau")

    def test_visible_main_slot_roles_agrees_with_the_band_policy(self):
        """Derived from role_topmost rather than listed again, so the two answers
        cannot drift: a main-slot player is in the band exactly when it shows."""
        for mode in ("nau", "hybrid", "genau"):
            assert visible_main_slot_roles(mode) == tuple(
                role for role in PRIMARY_SLOT_ROLES if role_topmost(role, mode)), mode

    def test_role_groups_partition_the_managed_set(self):
        assert set(MANAGED_ROLES) == {
            "rfb", "portrait", "landscape", "genau", "nau", "dashboard",
        }
        assert set(FIXED_TOPMOST_ROLES) == {"rfb", "portrait", "landscape", "dashboard"}
        assert set(PRIMARY_SLOT_ROLES) == {"nau", "genau"}
        # The two groups are disjoint and together cover every managed role.
        assert set(FIXED_TOPMOST_ROLES) & set(PRIMARY_SLOT_ROLES) == set()
        assert set(FIXED_TOPMOST_ROLES) | set(PRIMARY_SLOT_ROLES) == set(MANAGED_ROLES)
