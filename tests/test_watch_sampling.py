from __future__ import annotations

from pathlib import Path

from funestra.video_groups import normalize_path_key

from fun_time.command_value import parse_command
from fun_time.watch_sampling import WatchSampler
from fun_time.watch_stats import load_watch_stats


def make_sampler(tmp_path: Path) -> WatchSampler:
    return WatchSampler(
        main_funestra_status_file=tmp_path / "main_player_status.txt",
        satellite_status_files={2: tmp_path / "portrait_status.txt",
                                3: tmp_path / "landscape_status.txt"},
        stats_file=tmp_path / "watch_stats.json",
    )


def _publish(path: Path, video, *, fraction: float, image: bool = False) -> None:
    path.write_text(
        f"video={video}\nposition_ms={round(fraction * 1000)}\nduration_ms=1000\n"
        f"paused=0\nlocked=0\npicture={int(image)}\n",
        encoding="utf-8",
    )


def _a_video_whose_stats_survive_pruning(tmp_path: Path, name: str) -> Path:
    on_disk = tmp_path / name
    on_disk.write_text("x", encoding="utf-8")
    return on_disk


def test_a_video_watched_to_the_end_is_recorded_as_a_completion(tmp_path):
    video = _a_video_whose_stats_survive_pruning(tmp_path, "alpha.mp4")
    sampler = make_sampler(tmp_path)

    _publish(tmp_path / "portrait_status.txt", video, fraction=0.1)
    sampler.sample_due(now=100.0, omni_paused=False)
    _publish(tmp_path / "portrait_status.txt", video, fraction=0.9)
    sampler.sample_due(now=101.1, omni_paused=False)
    _publish(tmp_path / "portrait_status.txt", tmp_path / "beta.mp4", fraction=0.0)
    sampler.sample_due(now=102.2, omni_paused=False)

    stats = load_watch_stats(tmp_path / "watch_stats.json")
    assert stats[normalize_path_key(str(video))]["completions"] == 1


def test_an_image_on_a_satellite_books_nothing_however_far_its_hold_has_run(tmp_path):
    image = tmp_path / "scene one.png"
    image.write_text("x", encoding="utf-8")
    sampler = make_sampler(tmp_path)

    for now, fraction in ((100.0, 0.1), (101.1, 0.9)):
        _publish(tmp_path / "portrait_status.txt", image, fraction=fraction, image=True)
        sampler.sample_due(now=now, omni_paused=False)
    _publish(tmp_path / "portrait_status.txt", tmp_path / "next.png", fraction=0.0, image=True)
    sampler.sample_due(now=102.2, omni_paused=False)

    assert normalize_path_key(str(image)) not in load_watch_stats(tmp_path / "watch_stats.json")


def _publish_main_funestra(path: Path, video, *, position_ms: int, duration_ms: int,
                 paused: bool = False) -> None:
    path.write_text(
        f"video={video}\nposition_ms={position_ms}\nduration_ms={duration_ms}\n"
        f"loop_state=normal\npaused={'1' if paused else '0'}\n",
        encoding="utf-8",
    )


class TestSamplingCadence:
    def test_a_sample_inside_the_interval_is_not_taken_again(self, tmp_path):
        sampler = make_sampler(tmp_path)
        video = _a_video_whose_stats_survive_pruning(tmp_path, "alpha.mp4")

        _publish(tmp_path / "portrait_status.txt", video, fraction=0.9)
        sampler.sample_due(now=100.0, omni_paused=False)
        _publish(tmp_path / "portrait_status.txt", tmp_path / "beta.mp4", fraction=0.0)
        sampler.sample_due(now=100.1, omni_paused=False)

        assert load_watch_stats(tmp_path / "watch_stats.json") == {}

    def test_a_position_held_near_the_end_under_omnipause_is_no_completion(self, tmp_path):
        sampler = make_sampler(tmp_path)
        video = _a_video_whose_stats_survive_pruning(tmp_path, "alpha.mp4")

        _publish(tmp_path / "portrait_status.txt", video, fraction=0.9)
        sampler.sample_due(now=100.0, omni_paused=True)
        _publish(tmp_path / "portrait_status.txt", tmp_path / "beta.mp4", fraction=0.0)
        sampler.sample_due(now=101.0, omni_paused=False)

        assert load_watch_stats(tmp_path / "watch_stats.json") == {}

    def test_resuming_from_a_pause_does_not_sample_off_cadence(self, tmp_path):
        sampler = make_sampler(tmp_path)
        video = _a_video_whose_stats_survive_pruning(tmp_path, "alpha.mp4")

        _publish(tmp_path / "portrait_status.txt", video, fraction=0.9)
        sampler.sample_due(now=100.0, omni_paused=False)
        sampler.sample_due(now=100.6, omni_paused=True)
        _publish(tmp_path / "portrait_status.txt", tmp_path / "beta.mp4", fraction=0.0)
        sampler.sample_due(now=100.7, omni_paused=False)

        assert load_watch_stats(tmp_path / "watch_stats.json") == {}


class TestTheMainFunestra:
    def _sample(self, sampler, at: float) -> None:
        sampler.sample_due(now=at, omni_paused=False)

    def test_a_main_funestra_video_watched_to_the_end_then_departed_is_a_completion(self, tmp_path):
        sampler = make_sampler(tmp_path)
        watched = _a_video_whose_stats_survive_pruning(tmp_path, "watched.mp4")
        nextv = _a_video_whose_stats_survive_pruning(tmp_path, "next.mp4")
        status = tmp_path / "main_player_status.txt"

        _publish_main_funestra(status, watched, position_ms=9000, duration_ms=10000)
        self._sample(sampler, 100.0)
        _publish_main_funestra(status, nextv, position_ms=0, duration_ms=10000)
        self._sample(sampler, 101.0)

        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(str(watched))]["completions"] == 1

    def test_an_unknown_duration_yields_no_sample(self, tmp_path):
        sampler = make_sampler(tmp_path)
        early = _a_video_whose_stats_survive_pruning(tmp_path, "early.mp4")
        nextv = _a_video_whose_stats_survive_pruning(tmp_path, "next.mp4")
        status = tmp_path / "main_player_status.txt"

        _publish_main_funestra(status, early, position_ms=5000, duration_ms=0)
        self._sample(sampler, 100.0)
        _publish_main_funestra(status, nextv, position_ms=0, duration_ms=10000)
        self._sample(sampler, 101.0)

        assert normalize_path_key(str(early)) not in load_watch_stats(tmp_path / "watch_stats.json")

    def test_a_paused_main_funestra_is_not_watching(self, tmp_path):
        sampler = make_sampler(tmp_path)
        watched = _a_video_whose_stats_survive_pruning(tmp_path, "watched.mp4")
        nextv = _a_video_whose_stats_survive_pruning(tmp_path, "next.mp4")
        status = tmp_path / "main_player_status.txt"

        _publish_main_funestra(status, watched, position_ms=9000, duration_ms=10000, paused=True)
        self._sample(sampler, 100.0)
        _publish_main_funestra(status, nextv, position_ms=0, duration_ms=10000)
        self._sample(sampler, 101.0)

        assert normalize_path_key(str(watched)) not in load_watch_stats(tmp_path / "watch_stats.json")

    def test_the_blank_between_videos_is_not_a_departure(self, tmp_path):
        sampler = make_sampler(tmp_path)
        watched = _a_video_whose_stats_survive_pruning(tmp_path, "watched.mp4")
        status = tmp_path / "main_player_status.txt"

        _publish_main_funestra(status, watched, position_ms=9000, duration_ms=10000)
        self._sample(sampler, 100.0)
        _publish_main_funestra(status, "", position_ms=0, duration_ms=10000)
        self._sample(sampler, 101.0)

        assert normalize_path_key(str(watched)) not in load_watch_stats(tmp_path / "watch_stats.json")

    def test_next_marks_the_departed_main_funestra_video_as_a_skip(self, tmp_path):
        sampler = make_sampler(tmp_path)
        early = _a_video_whose_stats_survive_pruning(tmp_path, "early.mp4")
        nextv = _a_video_whose_stats_survive_pruning(tmp_path, "next.mp4")
        status = tmp_path / "main_player_status.txt"

        _publish_main_funestra(status, early, position_ms=1000, duration_ms=10000)
        self._sample(sampler, 100.0)
        sampler.note_command("main_next")
        _publish_main_funestra(status, nextv, position_ms=0, duration_ms=10000)
        self._sample(sampler, 101.0)

        stats = load_watch_stats(tmp_path / "watch_stats.json")
        assert stats[normalize_path_key(str(early))]["skips"] == 1

    def test_a_trashed_videos_departure_is_not_credited_as_a_completion(self, tmp_path):
        sampler = make_sampler(tmp_path)
        alpha = _a_video_whose_stats_survive_pruning(tmp_path, "alpha.mp4")

        _publish(tmp_path / "portrait_status.txt", alpha, fraction=0.9)
        sampler.sample_due(now=100.0, omni_paused=False)
        sampler.note_command("portrait_trash")
        _publish(tmp_path / "portrait_status.txt", tmp_path / "beta.mp4", fraction=0.0)
        sampler.sample_due(now=101.0, omni_paused=False)

        assert load_watch_stats(tmp_path / "watch_stats.json") == {}


class TestBackDating:
    def _observe(self, sampler, satellite: str, video: str, *, at: float) -> None:
        _publish(sampler.satellite_status_files[2 if satellite == "portrait" else 3],
                 video, fraction=0.1)
        sampler.sample_due(now=at, omni_paused=False)

    def test_a_spoken_command_is_about_the_video_on_screen_when_the_words_began(self, tmp_path):
        sampler = make_sampler(tmp_path)
        self._observe(sampler, "portrait", "C:\\clips\\meant.mp4", at=100.0)
        self._observe(sampler, "portrait", "C:\\clips\\advanced_to.mp4", at=101.0)

        assert sampler.video_at(parse_command("portrait_lock"), 100.2) == "C:\\clips\\meant.mp4"

    def test_each_satellite_reads_its_own_timeline(self, tmp_path):
        sampler = make_sampler(tmp_path)
        self._observe(sampler, "portrait", "C:\\clips\\portrait.mp4", at=100.0)
        self._observe(sampler, "landscape", "C:\\clips\\landscape.mp4", at=100.6)

        assert sampler.video_at(parse_command("landscape_trash"), 100.7) == "C:\\clips\\landscape.mp4"

    def test_a_satellite_showing_the_hosted_apps_images_is_back_dated_too(self, tmp_path):
        sampler = make_sampler(tmp_path)
        status = sampler.satellite_status_files[2]
        for at, image in ((100.0, "C:\\images\\meant.png"), (101.0, "C:\\images\\next.png")):
            status.write_text(f"video={image}\nposition_ms=0\nduration_ms=0\n", encoding="utf-8")
            sampler.sample_due(now=at, omni_paused=False)

        assert sampler.video_at(parse_command("portrait_lock_on"), 100.2) == "C:\\images\\meant.png"

    def test_a_hotkey_command_names_no_video(self, tmp_path):
        sampler = make_sampler(tmp_path)
        self._observe(sampler, "portrait", "C:\\clips\\meant.mp4", at=100.0)

        assert sampler.video_at(parse_command("portrait_trash"), None) == ""



def _a_video_the_slideshow_left_for_an_image(tmp_path, *, next_pressed_on: str) -> dict:
    video = tmp_path / "alpha.mp4"
    video.write_text("x", encoding="utf-8")
    status = tmp_path / "portrait_status.txt"
    sampler = make_sampler(tmp_path)
    _publish(status, video, fraction=0.3)
    sampler.sample_due(now=100.0, omni_paused=False)
    if next_pressed_on == "the video":
        sampler.note_command("portrait_next")
    _publish(status, tmp_path / "scene one.png", fraction=0.1, image=True)
    sampler.sample_due(now=101.1, omni_paused=False)
    if next_pressed_on == "the image":
        sampler.note_command("portrait_next")
    _publish(status, tmp_path / "beta.mp4", fraction=0.0)
    sampler.sample_due(now=102.2, omni_paused=False)
    return load_watch_stats(tmp_path / "watch_stats.json").get(normalize_path_key(str(video)), {})


def test_next_pressed_on_an_image_is_not_charged_to_the_video_before_it(tmp_path):
    assert _a_video_the_slideshow_left_for_an_image(tmp_path, next_pressed_on="the image") == {}


def test_next_pressed_on_a_video_is_a_skip_though_an_image_comes_next(tmp_path):
    booked = _a_video_the_slideshow_left_for_an_image(tmp_path, next_pressed_on="the video")

    assert booked["skips"] == 1
