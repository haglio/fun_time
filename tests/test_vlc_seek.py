"""Unit tests for PrimarySeekAccumulator — rapid-nudge seek stacking."""
from __future__ import annotations

from fun_time.vlc_seek import PrimarySeekAccumulator


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _make(positions, *, clock=None):
    """Build an accumulator over a fake VLC.

    *positions* is either a single ``(time, length)`` tuple returned on
    every read, or a list of tuples returned successively (last repeats).
    Returns ``(accumulator, seeks, reads)`` where *seeks* records issued
    absolute targets and *reads* counts position reads.
    """
    seeks: list[float] = []
    reads = {"n": 0}

    def read_position():
        i = reads["n"]
        reads["n"] += 1
        if isinstance(positions, list):
            return positions[min(i, len(positions) - 1)]
        return positions

    def seek(target: float) -> None:
        seeks.append(target)

    acc = PrimarySeekAccumulator(
        read_position=read_position, seek=seek, clock=clock or FakeClock()
    )
    return acc, seeks, reads


def test_single_forward_nudge_seeks_current_plus_ten():
    acc, seeks, reads = _make((50.0, 300.0))

    acc.nudge(1)

    assert seeks == [60.0]
    assert reads["n"] == 1


def test_rapid_forward_nudges_stack_without_rereading():
    # VLC's reported time never advances (stale/lagging), yet consecutive
    # nudges must stack onto our own running target — not re-read the base.
    acc, seeks, reads = _make((50.0, 300.0))

    acc.nudge(1)
    acc.nudge(1)
    acc.nudge(1)

    assert seeks == [60.0, 70.0, 80.0]
    assert reads["n"] == 1


def test_backward_nudges_clamp_at_zero_and_running_target_holds():
    # Near the start, backward nudges must clamp to 0 — and the running
    # target must clamp too, so a later forward nudge starts from 0, not
    # from a phantom negative position.
    acc, seeks, reads = _make((5.0, 300.0))

    acc.nudge(-1)
    acc.nudge(-1)

    assert seeks == [0.0, 0.0]
    assert reads["n"] == 1


def test_forward_nudges_clamp_at_length():
    acc, seeks, reads = _make((295.0, 300.0))

    acc.nudge(1)
    acc.nudge(1)

    assert seeks == [300.0, 300.0]


def test_length_zero_means_unknown_and_never_clamps_forward():
    # VLC reports length 0 for streams / not-yet-probed media.  Treating that
    # as an upper bound would clamp every forward seek to 0 — breaking nudging
    # entirely.  Unknown length must impose no ceiling.
    acc, seeks, reads = _make((50.0, 0.0))

    acc.nudge(1)
    acc.nudge(1)

    assert seeks == [60.0, 70.0]


def test_invalidate_forces_fresh_read_on_next_nudge():
    # When the primary video changes, the running target is meaningless —
    # invalidate() must drop it so the next nudge re-reads the new position.
    acc, seeks, reads = _make([(50.0, 300.0), (5.0, 120.0)])

    acc.nudge(1)          # base 50 -> 60
    acc.invalidate()
    acc.nudge(1)          # must re-read -> base 5 -> 15

    assert seeks == [60.0, 15.0]
    assert reads["n"] == 2


def test_target_expires_after_ttl_and_rereads_fresh_position():
    clock = FakeClock()
    acc, seeks, reads = _make([(50.0, 300.0), (200.0, 300.0)], clock=clock)

    acc.nudge(1)                       # base 50 -> 60
    clock.advance(2.1)                 # burst goes cold (TTL 2.0s)
    acc.nudge(1)                       # re-read -> base 200 -> 210

    assert seeks == [60.0, 210.0]
    assert reads["n"] == 2


def test_nudge_is_dropped_when_vlc_is_unreachable():
    # A fresh burst that can't read VLC issues no seek, and leaves no stale
    # target — the next successful read starts clean.
    acc, seeks, reads = _make([None, (50.0, 300.0)])

    acc.nudge(1)                       # read fails -> no seek
    acc.nudge(1)                       # reads again -> 60

    assert seeks == [60.0]
    assert reads["n"] == 2


def test_mixed_directions_net_within_a_burst():
    acc, seeks, reads = _make((50.0, 300.0))

    acc.nudge(1)
    acc.nudge(1)
    acc.nudge(-1)

    assert seeks == [60.0, 70.0, 60.0]
    assert reads["n"] == 1
