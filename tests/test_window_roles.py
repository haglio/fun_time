from __future__ import annotations

from fun_time.window_roles import MANAGED_ROLES, role_topmost


class TestRoleTopmost:
    """The topmost band is static for every window except the primary player
    Nau, whose band is mode-dependent: topmost in pure nau mode (it owns the
    whole display and floats above the desktop, like the primary player always
    has), but NON-topmost in hybrid (it rides under Genau's transparent HUD) and
    in genau mode (it is hidden)."""

    def test_nau_is_topmost_only_in_nau_mode(self):
        assert role_topmost("nau", "nau") is True
        assert role_topmost("nau", "hybrid") is False
        assert role_topmost("nau", "genau") is False

    def test_every_other_role_is_always_topmost(self):
        for role in ("rfb", "portrait", "landscape", "genau", "dashboard"):
            for mode in ("nau", "hybrid", "genau"):
                assert role_topmost(role, mode) is True, (role, mode)

    def test_managed_roles_is_the_whole_set(self):
        assert set(MANAGED_ROLES) == {
            "rfb", "portrait", "landscape", "genau", "nau", "dashboard",
        }
