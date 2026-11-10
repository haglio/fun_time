from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

MAIN_FUNESTRA = Path(__file__).resolve().parents[1] / "main_funestra"


def _take_identity():
    # Imported inside the test rather than at collection: importing the app
    # module pulls pygame in for real.
    from main_funestra.app import _take_fun_times_taskbar_identity  # noqa: PLC0415
    return _take_fun_times_taskbar_identity


class TestTheMainFunestraTakesTheIdentityItIsGiven:
    def test_told_one_it_takes_it(self):
        with patch("main_funestra.app.set_app_user_model_id") as claim:
            _take_identity()("Example.App")

        claim.assert_called_once_with("Example.App")

    def test_told_none_it_claims_nothing(self):
        with patch("main_funestra.app.set_app_user_model_id") as claim:
            _take_identity()(None)

        claim.assert_not_called()

    def test_a_refusal_never_stops_the_funestra_starting(self):
        with patch("main_funestra.app.set_app_user_model_id", side_effect=OSError) as claim:
            _take_identity()("Example.App")

        claim.assert_called_once_with("Example.App"), "it did ask, and swallowed the no"


def test_the_pin_that_carries_the_identity_stays_fun_times_to_stamp():
    stampers = [
        module.name for module in sorted(MAIN_FUNESTRA.glob("*.py"))
        if "stamp_pinned_shortcuts" in module.read_text(encoding="utf-8")
    ]

    assert not stampers, f"{stampers} stamp the pinned shortcut, which fun_time/orchestrator.py keeps"
