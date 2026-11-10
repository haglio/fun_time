from __future__ import annotations

import pytest

from fun_time.fun_time_funestras import FunTimeFunestra
from fun_time.origenerator_switches import RfbMode, hosts_a_satellite
from fun_time.shared_state import BridgeState, SatelliteState
from tests.test_windows_bridge_dispatch_loop import make_config


def _hosting(tmp_path):
    return make_config(tmp_path, origenerator_enabled=True,
                       origenerator_cmd_file=tmp_path / "origenerator_cmd.txt")


@pytest.mark.parametrize(("portrait", "landscape", "holds"), [
    (False, False, False),
    (True, False, True),
    (False, True, True),
    (True, True, True),
])
def test_a_hosting_session_holds_a_satellite_while_the_app_has_taken_either(
        tmp_path, portrait, landscape, holds):
    state = BridgeState(portrait=SatelliteState(handed_over=portrait),
                        landscape=SatelliteState(handed_over=landscape))

    assert hosts_a_satellite(state, _hosting(tmp_path)) is holds


def test_origenerator_in_the_browsers_place_holds_no_satellite(tmp_path):
    assert not hosts_a_satellite(BridgeState(rfb_mode=RfbMode.ORIGENERATOR), _hosting(tmp_path))


def test_a_session_hosting_no_origenerator_holds_no_satellite(tmp_path):
    state = BridgeState().with_satellite(FunTimeFunestra.PORTRAIT, handed_over=True)

    assert not hosts_a_satellite(state, make_config(tmp_path))
