from __future__ import annotations

from fun_time_vr.projection import (
    EQUIRECT_180_SBS,
    EQUIRECT_360,
    FISHEYE_180_SBS,
    FISHEYE_190_SBS,
    FISHEYE_200_EQUISOLID_SBS,
    FISHEYE_200_STEREOGRAPHIC_SBS,
    FISHEYE_220_SBS,
    FLAT,
    MKX200_SBS,
    NAMED_PROJECTIONS,
    RECTILINEAR_SBS,
)
from fun_time_vr.render import immersive_wrap
from fun_time_vr.wrap_readout import READOUT_EVERY_S, WrapReadout, label, readout


def test_every_projection_is_named_in_the_apps_own_words():
    assert {projection: label(projection) for projection in NAMED_PROJECTIONS} == {
        FLAT: "Flat",
        EQUIRECT_180_SBS: "180° SBS",
        FISHEYE_180_SBS: "Fisheye",
        FISHEYE_200_STEREOGRAPHIC_SBS: "Stereographic",
        FISHEYE_200_EQUISOLID_SBS: "Equisolid",
        RECTILINEAR_SBS: "Rectilinear",
        EQUIRECT_360: "360°",
        FISHEYE_190_SBS: "Fisheye 190",
        MKX200_SBS: "MKX200",
        FISHEYE_220_SBS: "Fisheye 220",
    }


def test_a_wrapped_picture_reads_its_projection_angle_and_height():
    wrap = immersive_wrap(FISHEYE_180_SBS, fov_deg=158.4, height=1.2)

    assert readout(FISHEYE_180_SBS, wrap) == "Fisheye · 158° · height 1.20"


def test_a_picture_nobody_dialed_reads_what_its_projection_carries():
    assert readout(EQUIRECT_180_SBS, immersive_wrap(EQUIRECT_180_SBS)) == "180° SBS · 180° · height 1.00"


def test_a_flat_picture_and_the_360_read_their_name_alone():
    assert readout(FLAT, immersive_wrap(FLAT)) == "Flat"
    assert readout(EQUIRECT_360, immersive_wrap(EQUIRECT_360)) == "360°"


class TestWhenTheReadoutFlashes:
    def test_the_first_reading_flashes_at_once(self):
        assert WrapReadout().frame("Fisheye · 158° · height 1.20", now=0.0) == "Fisheye · 158° · height 1.20"

    def test_the_same_reading_again_flashes_nothing(self):
        flashing = WrapReadout()
        flashing.frame("Flat", now=0.0)

        assert flashing.frame("Flat", now=5.0) is None

    def test_a_change_inside_the_window_waits_for_it_and_flashes_once(self):
        flashing = WrapReadout()
        flashing.frame("Fisheye · 100° · height 1.00", now=0.0)

        held = flashing.frame("Fisheye · 101° · height 1.00", now=READOUT_EVERY_S / 2)
        flashed = flashing.frame("Fisheye · 103° · height 1.00", now=READOUT_EVERY_S)
        again = flashing.frame("Fisheye · 103° · height 1.00", now=READOUT_EVERY_S * 1.5)

        assert (held, flashed, again) == (None, "Fisheye · 103° · height 1.00", None)

    def test_nothing_to_read_flashes_nothing_and_forgets_the_last_reading(self):
        flashing = WrapReadout()
        flashing.frame("Flat", now=0.0)

        assert flashing.frame(None, now=1.0) is None
        assert flashing.frame("Flat", now=2.0) == "Flat"
