"""Whose taskbar button the main player's window belongs to.

It is a window of the application the user actually launched, so Fun Time passes
its own AppUserModelID and the window takes that — without stamping anything,
since the pin carrying that identity is Fun Time's to keep up to date.
"""
from __future__ import annotations

from unittest.mock import patch


def _set_aumid():
    # Imported inside the test rather than at collection: importing the app
    # module pulls pygame in for real.
    from main_player.app import _set_aumid  # noqa: PLC0415
    return _set_aumid


class TestTheMainPlayerTakesTheIdentityItIsGiven:
    def test_told_one_it_takes_it(self):
        """The pinned shortcut carrying that identity is Fun Time's."""
        with patch("main_player.app.set_app_user_model_id") as claim:
            _set_aumid()("Example.App")

        claim.assert_called_once_with("Example.App")

    def test_told_none_it_claims_nothing(self):
        with patch("main_player.app.set_app_user_model_id") as claim:
            _set_aumid()(None)

        claim.assert_not_called()

    def test_a_refusal_never_stops_the_player_starting(self):
        """An icon is not worth failing to open a window over."""
        with patch("main_player.app.set_app_user_model_id", side_effect=OSError) as claim:
            _set_aumid()("Example.App")

        claim.assert_called_once_with("Example.App"), "it did ask, and swallowed the no"
