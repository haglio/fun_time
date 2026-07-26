from __future__ import annotations

import json

import pytest

from fun_time_vr.projection import (
    EQUIRECT_180_SBS,
    EQUIRECT_360,
    FISHEYE_190_SBS,
    FLAT,
    MKX200_SBS,
    PROJECTIONS,
    default_projection,
    next_projection,
    resolve_projection,
    save_projection,
    saved_projection,
)


@pytest.fixture
def library(tmp_path):
    """A fabricated library shaped like the real one: videos/videos mirrored by
    videos/metadata (the sidecar path rule derives one from the other)."""
    videos = tmp_path / "videos" / "videos"
    metadata = tmp_path / "videos" / "metadata"
    (videos / "VR" / "finished").mkdir(parents=True)
    (videos / "2D" / "non_AI").mkdir(parents=True)
    metadata.mkdir(parents=True)
    return videos, metadata


class TestDefaults:
    def test_video_under_a_vr_dir_defaults_to_equirect_180_sbs(self, library):
        videos, _ = library
        video = videos / "VR" / "finished" / "scene one.mp4"

        assert default_projection(str(video), [videos / "VR" / "finished"]) == EQUIRECT_180_SBS

    def test_2d_video_defaults_to_flat(self, library):
        videos, _ = library
        video = videos / "2D" / "non_AI" / "scene two.mp4"

        assert default_projection(str(video), [videos / "VR" / "finished"]) == FLAT

    def test_fisheye_filename_hint_wins(self, library):
        videos, _ = library
        video = videos / "VR" / "finished" / "scene three (fisheye) (1440).mp4"

        assert default_projection(str(video), [videos / "VR"]) == FISHEYE_190_SBS

    def test_mkx200_filename_hint_wins(self, library):
        videos, _ = library
        video = videos / "VR" / "finished" / "scene four MKX200.mp4"

        assert default_projection(str(video), [videos / "VR"]) == MKX200_SBS

    def test_180_filename_hint_reads_vr_even_outside_vr_dirs(self, library):
        videos, _ = library
        video = videos / "2D" / "non_AI" / "scene five_LR_180.mp4"

        assert default_projection(str(video), [videos / "VR"]) == EQUIRECT_180_SBS

    def test_1080p_name_is_not_mistaken_for_180(self, library):
        videos, _ = library
        video = videos / "2D" / "non_AI" / "scene six 1080p.mp4"

        assert default_projection(str(video), [videos / "VR"]) == FLAT

    def test_360_filename_hint(self, library):
        videos, _ = library
        video = videos / "VR" / "finished" / "scene seven_360.mp4"

        assert default_projection(str(video), [videos / "VR"]) == EQUIRECT_360


class TestCycle:
    def test_cycles_through_every_projection_and_wraps(self):
        seen = [FLAT]
        while True:
            step = next_projection(seen[-1])
            if step == FLAT:
                break
            seen.append(step)

        assert tuple(seen) == PROJECTIONS

    def test_unknown_value_restarts_the_cycle(self):
        assert next_projection("no_such_projection") == PROJECTIONS[0]


class TestSidecarPersistence:
    def test_save_creates_a_sidecar_in_the_metadata_mirror(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"

        assert save_projection(str(video), metadata, FISHEYE_190_SBS) is True

        sidecar = metadata / "VR" / "finished" / "scene one.json"
        assert json.loads(sidecar.read_text(encoding="utf-8")) == {
            "vr": {"projection": "fisheye_190_sbs"}
        }

    def test_save_merges_into_an_existing_sidecar_preserving_foreign_fields(self, library):
        videos, metadata = library
        video = videos / "2D" / "non_AI" / "scene two.mp4"
        sidecar = metadata / "2D" / "non_AI" / "scene two.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            json.dumps({"video": {"action": "alpha"}, "version": {"group": "g1"}}, indent=2) + "\n",
            encoding="utf-8",
        )

        save_projection(str(video), metadata, EQUIRECT_180_SBS)

        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        assert payload["video"] == {"action": "alpha"}
        assert payload["version"] == {"group": "g1"}
        assert payload["vr"] == {"projection": "equirect_180_sbs"}

    def test_save_outside_the_library_is_a_refusal_not_a_stray_file(self, library, tmp_path):
        _, metadata = library
        outsider = tmp_path / "elsewhere" / "scene.mp4"

        assert save_projection(str(outsider), metadata, FLAT) is False

    def test_saved_projection_reads_back(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        save_projection(str(video), metadata, MKX200_SBS)

        assert saved_projection(str(video), metadata) == MKX200_SBS

    def test_saved_projection_ignores_a_value_no_longer_in_the_cycle(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        sidecar = metadata / "VR" / "finished" / "scene one.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(json.dumps({"vr": {"projection": "retired_mode"}}), encoding="utf-8")

        assert saved_projection(str(video), metadata) is None

    def test_saved_projection_none_without_a_sidecar(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"

        assert saved_projection(str(video), metadata) is None


class TestResolve:
    def test_saved_choice_beats_the_default(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        save_projection(str(video), metadata, FLAT)

        assert resolve_projection(str(video), metadata, [videos / "VR"]) == FLAT

    def test_falls_back_to_the_default_when_nothing_is_saved(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"

        assert resolve_projection(str(video), metadata, [videos / "VR"]) == EQUIRECT_180_SBS

    def test_no_metadata_root_still_yields_a_default(self, library):
        videos, _ = library
        video = videos / "VR" / "finished" / "scene one.mp4"

        assert resolve_projection(str(video), None, [videos / "VR"]) == EQUIRECT_180_SBS
