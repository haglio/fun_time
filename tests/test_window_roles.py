from __future__ import annotations

from fun_time.window_roles import (
    FIXED_TOPMOST_ROLES,
    MANAGED_ROLES,
    PRIMARY_SLOT_ROLES,
    role_topmost,
)


class TestRoleTopmost:
    """Every managed window is topmost in every mode EXCEPT Nau, which is
    topmost only while it owns the display.  In hybrid both Nau and Genau are
    topmost (Genau's HUD stacked above Nau) — the stacking is enforced by
    promotion order, so ``role_topmost`` just says both belong in the band."""

    def test_nau_is_topmost_whenever_it_displays(self):
        # Nau owns the display in nau and hybrid, so it floats topmost in both.
        assert role_topmost("nau", "nau") is True
        assert role_topmost("nau", "hybrid") is True
        # In genau mode Nau is hidden and stays out of the band.
        assert role_topmost("nau", "genau") is False

    def test_every_other_role_is_always_topmost(self):
        for role in ("rfb", "portrait", "landscape", "genau", "dashboard"):
            for mode in ("nau", "hybrid", "genau"):
                assert role_topmost(role, mode) is True, (role, mode)

    def test_role_groups_partition_the_managed_set(self):
        assert set(MANAGED_ROLES) == {
            "rfb", "portrait", "landscape", "genau", "nau", "dashboard",
        }
        assert set(FIXED_TOPMOST_ROLES) == {"rfb", "portrait", "landscape", "dashboard"}
        assert set(PRIMARY_SLOT_ROLES) == {"nau", "genau"}
        # The two groups are disjoint and together cover every managed role.
        assert set(FIXED_TOPMOST_ROLES) & set(PRIMARY_SLOT_ROLES) == set()
        assert set(FIXED_TOPMOST_ROLES) | set(PRIMARY_SLOT_ROLES) == set(MANAGED_ROLES)
