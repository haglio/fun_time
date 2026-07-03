"""Tests for the centralized z-order module."""
from unittest.mock import patch

from fun_time.z_order import apply_z_order, compute_z_order


class TestComputeZOrder:
    def test_nau_mode_nau_topmost_others_not(self):
        """In nau mode, Nau is TOPMOST; Primary VLC and Genau are not."""
        layers = compute_z_order(
            primary_hwnd=100,
            genau_hwnd=200,
            nau_hwnd=300,
            primary_mode="nau",
        )
        assert [(h, t) for h, t in layers if h == 100] == [(100, False)]
        assert [(h, t) for h, t in layers if h == 200] == [(200, False)]
        assert [(h, t) for h, t in layers if h == 300] == [(300, True)]

    def test_genau_active_genau_topmost_others_not(self):
        """When Genau is active, Genau is TOPMOST; Primary and Nau are not."""
        layers = compute_z_order(
            primary_hwnd=100,
            genau_hwnd=200,
            nau_hwnd=300,
            primary_mode="genau",
        )
        assert [(h, t) for h, t in layers if h == 100] == [(100, False)]
        assert [(h, t) for h, t in layers if h == 200] == [(200, True)]
        assert [(h, t) for h, t in layers if h == 300] == [(300, False)]

    def test_full_stack_order_nau_mode(self):
        """Full stack bottom-to-top: RFB, Portrait, Landscape, Nau, Dashboard."""
        layers = compute_z_order(
            rfb_hwnd=1,
            portrait_hwnd=2,
            landscape_hwnd=3,
            primary_hwnd=4,
            genau_hwnd=5,
            nau_hwnd=6,
            dashboard_hwnd=7,
            primary_mode="nau",
        )
        topmost_hwnds = [h for h, t in layers if t]
        assert topmost_hwnds == [1, 2, 3, 6, 7]
        not_topmost = [h for h, t in layers if not t]
        assert sorted(not_topmost) == [4, 5]

    def test_full_stack_order_genau_active(self):
        """When Genau is active, it replaces Nau in the topmost stack."""
        layers = compute_z_order(
            rfb_hwnd=1,
            portrait_hwnd=2,
            landscape_hwnd=3,
            primary_hwnd=4,
            genau_hwnd=5,
            nau_hwnd=6,
            dashboard_hwnd=7,
            primary_mode="genau",
        )
        topmost_hwnds = [h for h, t in layers if t]
        assert topmost_hwnds == [1, 2, 3, 5, 7]
        not_topmost = [h for h, t in layers if not t]
        assert sorted(not_topmost) == [4, 6]

    def test_hybrid_vlc_and_genau_topmost_genau_above_primary(self):
        """In hybrid mode Primary VLC and Genau are topmost (Genau above); Nau is not."""
        layers = compute_z_order(
            rfb_hwnd=1,
            portrait_hwnd=2,
            landscape_hwnd=3,
            primary_hwnd=4,
            genau_hwnd=5,
            nau_hwnd=6,
            dashboard_hwnd=7,
            primary_mode="hybrid",
        )
        topmost_hwnds = [h for h, t in layers if t]
        assert topmost_hwnds == [1, 2, 3, 4, 5, 7]
        not_topmost = [h for h, t in layers if not t]
        assert not_topmost == [6]
        # Genau must come after Primary in the list (stacks on top)
        primary_idx = [h for h, _ in layers].index(4)
        genau_idx = [h for h, _ in layers].index(5)
        assert genau_idx > primary_idx

    def test_missing_hwnds_skipped(self):
        """Zero-valued HWNDs are omitted from layers."""
        layers = compute_z_order(
            primary_hwnd=100,
            nau_hwnd=200,
            primary_mode="nau",
        )
        hwnds = [h for h, _ in layers]
        assert 0 not in hwnds
        assert hwnds == [100, 200]

    def test_dashboard_always_last_topmost(self):
        """Dashboard must be the last TOPMOST entry regardless of mode."""
        for primary_mode in ["nau", "genau", "hybrid"]:
            layers = compute_z_order(
                primary_hwnd=1,
                genau_hwnd=2,
                nau_hwnd=3,
                dashboard_hwnd=4,
                primary_mode=primary_mode,
            )
            topmost_entries = [(h, t) for h, t in layers if t]
            assert topmost_entries[-1] == (4, True), f"primary_mode={primary_mode}"


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
