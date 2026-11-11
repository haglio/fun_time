from __future__ import annotations

import json
from contextlib import contextmanager

import numpy as np
from PIL import Image
from shared_ui.application import application
from shared_ui.palette import BLUE
from shared_ui.reference import narrowest_page_px

from fun_time.command_reference import build_reference_sections
from fun_time.hosted_contract import HostedVocabulary, publish_actions_heard_as
from fun_time_vr.reference_pages import draw_page, draw_the_pages, main
from fun_time_vr.reference_panel import PAGES_FILENAME

_SECTIONS = build_reference_sections()


def _accent_rows(image: Image.Image) -> np.ndarray:
    pixels = np.asarray(image.convert("RGB")).astype(int)
    near_the_accent = (np.abs(pixels - np.array(BLUE)).sum(axis=2) < 60).any(axis=1)
    return np.nonzero(near_the_accent)[0]


class TestEachPageIsTheDesktops:
    def test_every_page_is_as_wide_as_the_desktop_lays_the_whole_reference_out_at_its_narrowest(self):
        widths = {draw_page(section, _SECTIONS).image.width for section in _SECTIONS}

        assert widths == {narrowest_page_px(_SECTIONS)}

    def test_a_page_runs_as_long_as_its_section_does(self):
        heights = {section.title: draw_page(section, _SECTIONS).image.height for section in _SECTIONS}

        assert heights["Funestras"] > 3 * heights["Dashboard"]

    def test_the_title_is_drawn_in_the_desktops_title_color_above_where_the_title_ends(self):
        page = draw_page(_SECTIONS[0], _SECTIONS)

        assert _accent_rows(page.image).min() < page.title_ends_at
        assert 0 < page.title_ends_at < page.image.height // 2

    def test_a_page_keeps_its_sections_note(self):
        noted = next(section for section in _SECTIONS if section.note)

        assert draw_page(noted, _SECTIONS).note == noted.note

    def test_the_stick_steps_a_page_as_far_as_a_wheel_notch_scrolls_the_desktops(self):
        assert draw_the_pages(_SECTIONS[:1]).step_px > 0


class TestTheHelperProcess:
    def test_it_draws_each_section_its_session_was_told_of_and_says_so_last(self, tmp_path):
        publish_actions_heard_as(tmp_path, HostedVocabulary(actions_heard_as=(("alphaname", ("alphaname",)),)))
        out = tmp_path / "pages"

        assert main(["--state-dir", str(tmp_path), "--out", str(out)]) == 0

        told = json.loads((out / PAGES_FILENAME).read_text(encoding="utf-8"))
        assert [page["title"] for page in told["pages"]] == [
            section.title for section in build_reference_sections({"alphaname": ("alphaname",)})]
        for page in told["pages"]:
            assert Image.open(out / page["image"]).width == narrowest_page_px(_SECTIONS)

    def test_it_draws_on_the_platform_the_desktops_reference_runs_on_so_titles_take_its_size(self, tmp_path):
        asked_for: list[list[str]] = []

        @contextmanager
        def recording(argv):
            asked_for.append(argv)
            with application(argv) as app:
                yield app

        main(["--state-dir", str(tmp_path), "--out", str(tmp_path / "pages")], opened=recording)

        assert ["-platform" in argv for argv in asked_for] == [False]
