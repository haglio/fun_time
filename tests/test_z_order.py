"""Tests for the centralized z-order module."""
from unittest.mock import patch

from fun_time.z_order import apply_z_order, compute_z_order


class TestComputeZOrder:
    def test_genau_inactive_primary_topmost_genau_not(self):
        """When Genau is not active, Primary is TOPMOST and Genau is not."""
        layers = compute_z_order(
            primary_hwnd=100,
            genau_hwnd=200,
            genau_active=False,
        )
        primary_entry = [(h, t) for h, t in layers if h == 100]
        genau_entry = [(h, t) for h, t in layers if h == 200]
        assert primary_entry == [(100, True)]
        assert genau_entry == [(200, False)]

    def test_genau_active_genau_topmost_primary_not(self):
        """When Genau is active, Genau is TOPMOST and Primary is not."""
        layers = compute_z_order(
            primary_hwnd=100,
            genau_hwnd=200,
            genau_active=True,
        )
        primary_entry = [(h, t) for h, t in layers if h == 100]
        genau_entry = [(h, t) for h, t in layers if h == 200]
        assert primary_entry == [(100, False)]
        assert genau_entry == [(200, True)]

    def test_full_stack_order_genau_inactive(self):
        """Full stack bottom-to-top: RFB, Portrait, Landscape, Primary, MFP, Dashboard."""
        layers = compute_z_order(
            rfb_hwnd=1,
            portrait_hwnd=2,
            landscape_hwnd=3,
            primary_hwnd=4,
            genau_hwnd=5,
            mfp_hwnd=6,
            dashboard_hwnd=7,
            genau_active=False,
        )
        topmost_hwnds = [h for h, t in layers if t]
        assert topmost_hwnds == [1, 2, 3, 4, 6, 7]
        # Genau is the only non-topmost entry
        not_topmost = [h for h, t in layers if not t]
        assert not_topmost == [5]

    def test_full_stack_order_genau_active(self):
        """When Genau is active, it replaces Primary in the topmost stack."""
        layers = compute_z_order(
            rfb_hwnd=1,
            portrait_hwnd=2,
            landscape_hwnd=3,
            primary_hwnd=4,
            genau_hwnd=5,
            mfp_hwnd=6,
            dashboard_hwnd=7,
            genau_active=True,
        )
        topmost_hwnds = [h for h, t in layers if t]
        assert topmost_hwnds == [1, 2, 3, 5, 6, 7]
        not_topmost = [h for h, t in layers if not t]
        assert not_topmost == [4]

    def test_missing_hwnds_skipped(self):
        """Zero-valued HWNDs are omitted from layers."""
        layers = compute_z_order(
            primary_hwnd=100,
            mfp_hwnd=200,
            genau_active=False,
        )
        hwnds = [h for h, _ in layers]
        assert 0 not in hwnds
        assert hwnds == [100, 200]

    def test_dashboard_always_last_topmost(self):
        """Dashboard must be the last TOPMOST entry regardless of genau state."""
        for genau_active in [False, True]:
            layers = compute_z_order(
                primary_hwnd=1,
                genau_hwnd=2,
                mfp_hwnd=3,
                dashboard_hwnd=4,
                genau_active=genau_active,
            )
            topmost_entries = [(h, t) for h, t in layers if t]
            assert topmost_entries[-1] == (4, True), f"genau_active={genau_active}"


class TestApplyZOrder:
    def test_demotes_all_then_promotes_in_order(self):
        """apply_z_order must demote all windows first, then promote bottom-to-top."""
        layers = [(10, True), (20, False), (30, True)]
        calls: list[tuple[int, bool]] = []

        with patch("fun_time.z_order.set_always_on_top", side_effect=lambda h, v: calls.append((h, v))):
            apply_z_order(layers)

        # First 3 calls: demote all
        assert calls[:3] == [(10, False), (20, False), (30, False)]
        # Next 2 calls: promote topmost entries only, in order
        assert calls[3:] == [(10, True), (30, True)]

    def test_skips_zero_hwnds(self):
        """Zero-valued HWNDs are not passed to set_always_on_top."""
        layers = [(0, True), (10, True)]
        calls: list[tuple[int, bool]] = []

        with patch("fun_time.z_order.set_always_on_top", side_effect=lambda h, v: calls.append((h, v))):
            apply_z_order(layers)

        hwnds_called = [h for h, _ in calls]
        assert 0 not in hwnds_called

    def test_enforce_skips_correct_windows(self):
        """With reorder=False, windows already in the correct state are skipped."""
        layers = [(10, True), (20, False), (30, True)]
        calls: list[tuple[int, bool]] = []

        # Simulate: 10 is already topmost (correct), 20 is topmost (wrong),
        # 30 is already topmost (correct)
        topmost_state = {10: True, 20: True, 30: True}

        with patch("fun_time.z_order.is_window_topmost", side_effect=lambda h: topmost_state.get(h, False)), \
             patch("fun_time.z_order.set_always_on_top", side_effect=lambda h, v: calls.append((h, v))):
            apply_z_order(layers, reorder=False)

        # Only hwnd 20 should be changed (topmost but should not be)
        assert calls == [(20, False)]

    def test_enforce_promotes_missing_topmost(self):
        """With reorder=False, a window that should be topmost but isn't gets promoted."""
        layers = [(10, True), (20, True)]
        calls: list[tuple[int, bool]] = []

        # 10 is not topmost (wrong), 20 is already topmost (correct)
        topmost_state = {10: False, 20: True}

        with patch("fun_time.z_order.is_window_topmost", side_effect=lambda h: topmost_state.get(h, False)), \
             patch("fun_time.z_order.set_always_on_top", side_effect=lambda h, v: calls.append((h, v))):
            apply_z_order(layers, reorder=False)

        assert calls == [(10, True)]
