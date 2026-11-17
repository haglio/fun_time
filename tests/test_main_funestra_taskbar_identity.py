from __future__ import annotations

from pathlib import Path

from main_funestra.window import (
    claim_the_sessions_taskbar_identity,
    wear_the_sessions_taskbar_button,
)

MAIN_FUNESTRA = Path(__file__).resolve().parents[1] / "main_funestra"


class TestAFunestraTakesTheIdentityItIsGiven:
    def test_told_one_it_takes_it(self):
        claimed = []

        claim_the_sessions_taskbar_identity("Example.App", claim=claimed.append)

        assert claimed == ["Example.App"]

    def test_told_none_it_claims_nothing(self):
        claimed = []

        claim_the_sessions_taskbar_identity(None, claim=claimed.append)

        assert claimed == []

    def test_a_refusal_never_stops_the_funestra_starting(self):
        def refuse(_identity):
            raise OSError("refused")

        claim_the_sessions_taskbar_identity("Example.App", claim=refuse)


def test_the_pin_that_carries_the_identity_stays_fun_times_to_stamp():
    stampers = [
        module.name for module in sorted(MAIN_FUNESTRA.glob("*.py"))
        if "stamp_pinned_shortcuts" in module.read_text(encoding="utf-8")
    ]

    assert not stampers, f"{stampers} stamp the pinned shortcut, which fun_time/orchestrator.py keeps"


class TestAFunestrasWindowWearsTheSessionsButton:
    def _wear(self, identity, *, dress, described=lambda _identity: None):
        wear_the_sessions_taskbar_button(4242, identity, dress=dress, described=described)

    def test_a_preview_window_wears_the_name_and_icon_registered_for_its_button(self):
        registered = object()
        dressed = []

        self._wear("Example.App.Preview", dress=lambda *worn: dressed.append(worn),
                   described={"Example.App.Preview": registered}.get)

        assert dressed == [(4242, "Example.App.Preview", registered)]

    def test_told_no_identity_it_leaves_the_window_as_it_is(self):
        dressed = []

        self._wear(None, dress=lambda *worn: dressed.append(worn))

        assert dressed == []

    def test_a_refusal_never_stops_the_funestra_starting(self):
        def refuse(*_worn):
            raise OSError("refused")

        self._wear("Example.App", dress=refuse)
