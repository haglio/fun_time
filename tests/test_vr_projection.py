from __future__ import annotations

import json

import pytest

from fun_time_vr.picture_shape import FISHEYE_CIRCLE, FULL_FRAME
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
    PROJECTIONS,
    RECTILINEAR_SBS,
    ProjectionMemory,
    default_projection,
    next_projection,
    previous_projection,
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
    def test_the_stops_are_the_mappings_and_the_angle_is_dialed_on_each(self):
        assert PROJECTIONS == (
            FLAT, EQUIRECT_180_SBS, FISHEYE_180_SBS, FISHEYE_200_STEREOGRAPHIC_SBS,
            FISHEYE_200_EQUISOLID_SBS, RECTILINEAR_SBS, EQUIRECT_360)

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

    def test_stepping_back_walks_the_same_ring_the_other_way(self):
        for projection in PROJECTIONS:
            assert previous_projection(next_projection(projection)) == projection


class TestSidecarPersistence:
    def test_save_creates_a_sidecar_in_the_metadata_mirror(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"

        assert ProjectionMemory(metadata).save(str(video), FISHEYE_190_SBS) is True

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

        ProjectionMemory(metadata).save(str(video), EQUIRECT_180_SBS)

        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        assert payload["video"] == {"action": "alpha"}
        assert payload["version"] == {"group": "g1"}
        assert payload["vr"] == {"projection": "equirect_180_sbs"}

    def test_save_outside_the_library_is_a_refusal_not_a_stray_file(self, library, tmp_path):
        _, metadata = library
        outsider = tmp_path / "elsewhere" / "scene.mp4"

        assert ProjectionMemory(metadata).save(str(outsider), FLAT) is False

    def test_saved_projection_reads_back(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        memory = ProjectionMemory(metadata)
        memory.save(str(video), MKX200_SBS)

        assert memory.saved(str(video)) == MKX200_SBS

    def test_saved_projection_ignores_a_value_no_longer_in_the_cycle(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        sidecar = metadata / "VR" / "finished" / "scene one.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(json.dumps({"vr": {"projection": "retired_mode"}}), encoding="utf-8")

        assert ProjectionMemory(metadata).saved(str(video)) is None

    def test_saved_projection_none_without_a_sidecar(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"

        assert ProjectionMemory(metadata).saved(str(video)) is None

    def test_a_choice_for_a_video_in_a_vr_folder_on_another_drive_is_remembered(self, tmp_path):
        """Where every one of his VR videos is: the folder exists only on the
        cloud drive, so none of them is beside the metadata root, and a choice
        made for one was dropped with a line in the log."""
        metadata = tmp_path / "local" / "videos" / "metadata"
        metadata.mkdir(parents=True)
        vr_dir = tmp_path / "cloud" / "videos" / "videos" / "VR" / "finished"
        video = vr_dir / "scene one.mp4"
        memory = ProjectionMemory(metadata, (vr_dir,))

        assert memory.save(str(video), MKX200_SBS) is True

        assert (metadata / "VR" / "finished" / "scene one.json").is_file()
        assert memory.resolve(str(video)) == MKX200_SBS


class TestResolve:
    def test_saved_choice_beats_the_default(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        memory = ProjectionMemory(metadata, (videos / "VR",))
        memory.save(str(video), FLAT)

        assert memory.resolve(str(video)) == FLAT

    def test_falls_back_to_the_default_when_nothing_is_saved(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"

        assert ProjectionMemory(metadata, (videos / "VR",)).resolve(str(video)) == EQUIRECT_180_SBS

    def test_no_metadata_root_still_yields_a_default(self, library):
        videos, _ = library
        video = videos / "VR" / "finished" / "scene one.mp4"

        assert ProjectionMemory(None, (videos / "VR",)).resolve(str(video)) == EQUIRECT_180_SBS


class TestWhatThePictureShowed:
    def test_a_video_whose_picture_is_a_circle_opens_as_a_fisheye(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        memory = ProjectionMemory(metadata, (videos / "VR",))

        memory.note_shape(str(video), FISHEYE_CIRCLE)

        assert memory.resolve(str(video)) == FISHEYE_180_SBS

    def test_his_own_choice_outranks_what_the_picture_showed(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        memory = ProjectionMemory(metadata, (videos / "VR",))
        memory.note_shape(str(video), FISHEYE_CIRCLE)

        memory.save(str(video), EQUIRECT_180_SBS)

        assert memory.resolve(str(video)) == EQUIRECT_180_SBS

    def test_a_name_that_says_which_fisheye_it_is_outranks_the_picture(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene three (fisheye).mp4"
        memory = ProjectionMemory(metadata, (videos / "VR",))

        memory.note_shape(str(video), FISHEYE_CIRCLE)

        assert memory.resolve(str(video)) == FISHEYE_190_SBS

    def test_a_picture_that_fills_the_frame_leaves_the_default_alone(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        memory = ProjectionMemory(metadata, (videos / "VR",))

        memory.note_shape(str(video), FULL_FRAME)

        assert memory.resolve(str(video)) == EQUIRECT_180_SBS

    def test_both_things_kept_about_a_video_share_its_one_record(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        memory = ProjectionMemory(metadata, (videos / "VR",))

        memory.save(str(video), MKX200_SBS)
        memory.note_shape(str(video), FISHEYE_CIRCLE)

        sidecar = metadata / "VR" / "finished" / "scene one.json"
        assert json.loads(sidecar.read_text(encoding="utf-8")) == {
            "vr": {"projection": "mkx200_sbs", "picture": "fisheye_circle"}
        }


class TestTheFieldOfViewAndHeightDialedForAVideo:
    def test_a_video_nobody_has_dialed_has_neither(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"

        memory = ProjectionMemory(metadata)

        assert memory.saved_fov(str(video)) is None
        assert memory.saved_height(str(video)) is None

    def test_both_read_back(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        memory = ProjectionMemory(metadata)

        assert memory.save_fov(str(video), 143.0) is True
        assert memory.save_height(str(video), 1.25) is True

        assert memory.saved_fov(str(video)) == 143.0
        assert memory.saved_height(str(video)) == 1.25

    def test_they_share_the_one_record_with_the_projection(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        memory = ProjectionMemory(metadata, (videos / "VR",))

        memory.save(str(video), FISHEYE_180_SBS)
        memory.save_fov(str(video), 143.0)
        memory.save_height(str(video), 1.25)

        sidecar = metadata / "VR" / "finished" / "scene one.json"
        assert json.loads(sidecar.read_text(encoding="utf-8")) == {
            "vr": {"projection": "fisheye_180_sbs", "fov": 143.0, "height": 1.25}
        }

    def test_what_is_not_a_positive_number_reads_as_unset(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        sidecar = metadata / "VR" / "finished" / "scene one.json"
        sidecar.parent.mkdir(parents=True)
        for kept in ("wide", -3, 0, True, None, float("inf")):
            sidecar.write_text(json.dumps({"vr": {"fov": kept, "height": kept}}), encoding="utf-8")

            memory = ProjectionMemory(metadata)

            assert memory.saved_fov(str(video)) is None, kept
            assert memory.saved_height(str(video)) is None, kept


class TestWhichVideosAreWorthALook:
    def test_a_vr_video_nobody_has_chosen_for_or_looked_at(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"

        assert ProjectionMemory(metadata, (videos / "VR",)).wants_a_look(str(video))

    def test_not_one_already_looked_at(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        memory = ProjectionMemory(metadata, (videos / "VR",))
        memory.note_shape(str(video), FULL_FRAME)

        assert not memory.wants_a_look(str(video))

    def test_not_one_he_has_chosen_for(self, library):
        videos, metadata = library
        video = videos / "VR" / "finished" / "scene one.mp4"
        memory = ProjectionMemory(metadata, (videos / "VR",))
        memory.save(str(video), FISHEYE_220_SBS)

        assert not memory.wants_a_look(str(video))

    def test_not_a_flat_video_or_one_whose_name_settles_it(self, library):
        videos, metadata = library
        memory = ProjectionMemory(metadata, (videos / "VR",))

        assert not memory.wants_a_look(str(videos / "2D" / "non_AI" / "scene two.mp4"))
        assert not memory.wants_a_look(str(videos / "VR" / "finished" / "scene four MKX200.mp4"))
        assert not memory.wants_a_look(str(videos / "VR" / "finished" / "scene seven_360.mp4"))
