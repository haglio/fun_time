from __future__ import annotations

from unittest.mock import patch


def _set_aumid():
    # Imported inside the test rather than at collection: importing the app
    # module pulls pygame in for real.
    from main_funestra.app import _set_aumid  # noqa: PLC0415
    return _set_aumid


class TestTheMainFunestraTakesTheIdentityItIsGiven:
    def test_told_one_it_takes_it(self):
        with patch("main_funestra.app.set_app_user_model_id") as claim:
            _set_aumid()("Example.App")

        claim.assert_called_once_with("Example.App")

    def test_told_none_it_claims_nothing(self):
        with patch("main_funestra.app.set_app_user_model_id") as claim:
            _set_aumid()(None)

        claim.assert_not_called()

    def test_a_refusal_never_stops_the_funestra_starting(self):
        with patch("main_funestra.app.set_app_user_model_id", side_effect=OSError) as claim:
            _set_aumid()("Example.App")

        claim.assert_called_once_with("Example.App"), "it did ask, and swallowed the no"
