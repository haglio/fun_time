from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .loopback_server import LOOPBACK_PORT

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_DIR / "fun_time_config.json"
EXAMPLE_CONFIG_PATH = PROJECT_DIR / "fun_time_config.example.json"


def _resolve_path(project_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (project_dir / path).resolve()


def _require_dict(parent: dict[str, Any], key: str, source_path: Path, context: str = "config") -> dict[str, Any]:
    value = parent.get(key)
    dotted = f"{context}.{key}" if context else key
    if value is None:
        raise ValueError(f"Missing required config section: {dotted} (in {source_path})")
    if not isinstance(value, dict):
        raise TypeError(f"Expected object for config section: {dotted} (in {source_path})")
    return value


def _require_value(parent: dict[str, Any], key: str, source_path: Path, context: str) -> Any:
    value = parent.get(key)
    dotted = f"{context}.{key}"
    if value is None:
        raise ValueError(f"Missing required config value: {dotted} (in {source_path})")
    return value


@dataclass(frozen=True)
class PathsConfig:
    ahk_exe: Path
    python_exe: Path
    nau_library_dirs: tuple[Path, ...]
    portrait_dirs: tuple[Path, ...]
    landscape_dirs: tuple[Path, ...]
    weird_dir: Path
    clips_dir: Path
    audio_dir: Path
    favs_file: Path
    state_dir: Path
    # Where the machine's one OSR2 broker keeps its own channel: the heartbeat and
    # serial-activity stamps it writes, and the command, mode and permission files
    # it reads.  Those files are the *broker's*, not the session's — ``../broker``
    # resolves them from its own config, which names one directory for good — so a
    # session that moves its ``state_dir`` has to keep pointing here.  Absent from
    # the config it simply is ``state_dir``, which is true of every session but a
    # branch one (see :mod:`fun_time.branch_session`), and of an integration run,
    # whose broker is a temp-dir one of its own.
    broker_state_dir: Path
    genau_python_exe: Path | None = None
    genau_config_path: Path | None = None
    # Which checkouts Genau and Nau are run out of, put on their PYTHONPATH.
    # Absent, every package they import — their own, and ``player_core`` under
    # them — resolves through the genau venv's editable installs, which name the
    # primary checkout of each repo for good: so a *worktree* of either could not
    # be run at all, and a branch of one could only be judged by landing it.
    # Several, because a change is often in two of them at once.  Empty in
    # ordinary use, which is what every session did before this.
    genau_project_dirs: tuple[Path, ...] = ()
    broker_tray_launcher: Path | None = None
    # The Origenerator checkout the session hosts on its satellite side (see
    # fun_time.satellites_mode), and the python that runs it — origenerator has
    # no venv of its own; its deps live in the system install its launcher
    # finds.  Absent, the satellites have no Origenerator mode at all.
    origenerator_dir: Path | None = None
    origenerator_python_exe: Path | None = None


@dataclass(frozen=True)
class LayoutConfig:
    primary_monitor: int
    secondary_monitor: int
    main_top_ratio: float
    landscape_width_ratio: float


@dataclass(frozen=True)
class AudioCompanionConfig:
    host: str
    port: int


@dataclass(frozen=True)
class RandomFavsBrowserConfig:
    enabled: bool
    shortcut_path: Path
    user_data_dir: Path
    profile_name: str
    open_count: int
    lazy_load: bool


@dataclass(frozen=True)
class RegenConfig:
    generate_video_url: str = "https://example.com/video"
    generate_image_url: str = "https://example.com/create"
    media_root: Path | None = None
    metadata_root: Path | None = None


@dataclass(frozen=True)
class VoiceControlConfig:
    enabled: bool
    model_path: str
    device_name: str | None = None
    sample_rate: int = 16000
    confidence_threshold: float = 0.7


@dataclass(frozen=True)
class VrConfig:
    """What FunTimeVR needs beyond the desktop session's own config.

    ``library_dirs`` joins the main rotation alongside ``nau_library_dirs``
    (the VR-mastered videos live in their own branch of the library);
    ``audio_device`` routes the main player's sound to the headset by substring
    match; the T-Code endpoint is the broker's UDP inlet, the same one Nau and
    Genau send to.  ``compositor_layers`` hands flat screens to the runtime's
    compositor as quad layers; off by default because the bundled "Pimax
    OpenXR 0.1.0" runtime accepts quad layers in xrEndFrame and then never
    composites them — screens submitted that way simply don't appear.
    """

    library_dirs: tuple[Path, ...] = ()
    audio_device: str | None = None
    tcode_udp_host: str = "127.0.0.1"
    tcode_udp_port: int = 50557
    compositor_layers: bool = False


@dataclass(frozen=True)
class ProjectConfig:
    project_dir: Path
    config_path: Path
    paths: PathsConfig
    layout: LayoutConfig
    audio_companion: AudioCompanionConfig
    random_favs_browser: RandomFavsBrowserConfig
    voice_control: VoiceControlConfig
    regen: RegenConfig
    # The one port this session serves on, and the last fixed one it claims.
    # Named here so a second session can be given one of its own; the default is
    # what the userscript's @updateURL is pinned to.
    loopback_port: int = LOOPBACK_PORT
    # FunTimeVR's additions; the desktop session never reads them.
    vr: VrConfig = VrConfig()
    # Config key ``instance_id``; read through the property below.
    instance_id_override: str | None = None

    @property
    def instance_id(self) -> str:
        """Which running session this one *is*, for the single-instance mutex.

        Defaults to the config path, so every config is its own instance —
        which is what lets integration runs, each on a unique temp config, take
        mutexes without colliding.  A config may instead name another session's
        identity, and then the two can never both be up: whichever starts
        second is refused with Fun Time's own "already running" message.  That
        is how a branch-verification session guarantees it replaces the live
        session rather than fighting it for the AHK shell, the monitors and the
        machine's fixed ports (see :mod:`fun_time.branch_session`).
        """
        return self.instance_id_override or str(self.config_path)

    # --- The broker's channel.  Every one of these is a file ../broker opens by
    # its own config, so they follow the broker's state dir rather than ours.
    @property
    def genau_mode_file(self) -> Path:
        """The broker's "Genau has the OSR2" flag — written by it, read by us."""
        return self.paths.broker_state_dir / "genau_mode.txt"

    @property
    def genau_enabled_file(self) -> Path:
        """Whether the broker may hand the OSR2 to Genau at all — our switch, its read."""
        return self.paths.broker_state_dir / "genau_enabled.txt"

    @property
    def broker_cmd_file(self) -> Path:
        """The one verb the broker consumes per tick (park, retract, resume)."""
        return self.paths.broker_state_dir / "broker_cmd.txt"

    @property
    def broker_heartbeat_file(self) -> Path:
        """Stamped every half second while the broker holds the serial port."""
        return self.paths.broker_state_dir / "broker_heartbeat.txt"

    @property
    def osr2_serial_rx_file(self) -> Path:
        """Stamped when the OSR2 last spoke — which is how we know it is powered on."""
        return self.paths.broker_state_dir / "osr2_serial_rx.txt"

    @property
    def genau_cmd_file(self) -> Path:
        return self.paths.state_dir / "genau_cmd.txt"

    @property
    def genau_paused_file(self) -> Path:
        return self.paths.state_dir / "genau_paused.txt"

    @property
    def nau_cmd_file(self) -> Path:
        return self.paths.state_dir / "nau_cmd.txt"

    @property
    def nau_paused_file(self) -> Path:
        return self.paths.state_dir / "nau_paused.txt"

    @property
    def nau_status_file(self) -> Path:
        return self.paths.state_dir / "nau_status.txt"

    @property
    def nau_notice_file(self) -> Path:
        return self.paths.state_dir / "nau_notice.txt"

    @property
    def nau_playlist_file(self) -> Path:
        return self.paths.state_dir / "nau_playlist.tsv"

    # --- The hosted Origenerator's channel (see fun_time.satellites_mode):
    # verbs in, the OmniPause flag over it, and region occupancy back.
    @property
    def origenerator_cmd_file(self) -> Path:
        return self.paths.state_dir / "origenerator_cmd.txt"

    @property
    def origenerator_paused_file(self) -> Path:
        return self.paths.state_dir / "origenerator_paused.txt"

    @property
    def origenerator_status_file(self) -> Path:
        return self.paths.state_dir / "origenerator_status.txt"

    @property
    def audio_paused_file(self) -> Path:
        return self.paths.state_dir / "audio_paused.txt"

    @property
    def audio_volume_file(self) -> Path:
        return self.paths.state_dir / "audio_volume.txt"

    @property
    def random_favs_browser_manifest_file(self) -> Path:
        return self.paths.state_dir / "random_favs_browser_urls.txt"

    @property
    def logs_dir(self) -> Path:
        return self.paths.state_dir

    def log_file(self, name: str) -> Path:
        return self.logs_dir / f"{name}.log"


def _resolve_config_path(config_path: str | Path | None, project_dir: Path) -> Path:
    path = Path(config_path).expanduser() if config_path else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = (project_dir / path).resolve()
    return path


def _require_optional_dict(parent: dict[str, Any], key: str, source_path: Path) -> dict[str, Any] | None:
    value = parent.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"Expected object for config section: {key} (in {source_path})")
    return value


def _require_path_value(
    parent: dict[str, Any], key: str, source_path: Path, context: str, project_dir: Path
) -> Path:
    return _resolve_path(project_dir, _require_value(parent, key, source_path, context))


def _require_typed_value(
    parent: dict[str, Any],
    key: str,
    source_path: Path,
    context: str,
    cast: type,
):
    return cast(_require_value(parent, key, source_path, context))


def _load_paths_config(paths_raw: dict[str, Any], source_path: Path, project_dir: Path) -> PathsConfig:
    nau_library_dirs_raw = _require_value(paths_raw, "nau_library_dirs", source_path, "config.paths")
    if not isinstance(nau_library_dirs_raw, list):
        raise TypeError("paths.nau_library_dirs must be a list of folder paths")
    if not nau_library_dirs_raw:
        raise ValueError("paths.nau_library_dirs must include at least one folder path")

    state_dir = _require_path_value(paths_raw, "state_dir", source_path, "config.paths", project_dir)
    return PathsConfig(
        ahk_exe=_require_path_value(paths_raw, "ahk_exe", source_path, "config.paths", project_dir),
        python_exe=_require_path_value(paths_raw, "python_exe", source_path, "config.paths", project_dir),
        nau_library_dirs=tuple(_resolve_path(project_dir, str(value)) for value in nau_library_dirs_raw),
        portrait_dirs=_load_dir_list(paths_raw, "portrait_dirs", "portrait_dir", source_path, project_dir),
        landscape_dirs=_load_dir_list(paths_raw, "landscape_dirs", "landscape_dir", source_path, project_dir),
        weird_dir=_require_path_value(paths_raw, "weird_dir", source_path, "config.paths", project_dir),
        clips_dir=_require_path_value(paths_raw, "clips_dir", source_path, "config.paths", project_dir),
        audio_dir=_require_path_value(paths_raw, "audio_dir", source_path, "config.paths", project_dir),
        favs_file=_require_path_value(paths_raw, "favs_file", source_path, "config.paths", project_dir),
        state_dir=state_dir,
        broker_state_dir=_resolve_path(project_dir, paths_raw["broker_state_dir"]) if paths_raw.get("broker_state_dir") else state_dir,
        genau_python_exe=_resolve_path(project_dir, paths_raw["genau_python_exe"]) if paths_raw.get("genau_python_exe") else None,
        genau_config_path=_resolve_path(project_dir, paths_raw["genau_config_path"]) if paths_raw.get("genau_config_path") else None,
        genau_project_dirs=tuple(
            _resolve_path(project_dir, str(value))
            for value in paths_raw.get("genau_project_dirs", [])),
        broker_tray_launcher=_resolve_path(project_dir, paths_raw["broker_tray_launcher"]) if paths_raw.get("broker_tray_launcher") else None,
        origenerator_dir=_resolve_path(project_dir, paths_raw["origenerator_dir"]) if paths_raw.get("origenerator_dir") else None,
        origenerator_python_exe=_resolve_path(project_dir, paths_raw["origenerator_python_exe"]) if paths_raw.get("origenerator_python_exe") else None,
    )


def _load_layout_config(layout_raw: dict[str, Any], source_path: Path) -> LayoutConfig:
    primary_monitor = layout_raw.get("primary_monitor")
    secondary_monitor = layout_raw.get("secondary_monitor")
    if primary_monitor is None:
        raise ValueError(f"Missing required config value: config.layout.primary_monitor (in {source_path})")
    if secondary_monitor is None:
        raise ValueError(f"Missing required config value: config.layout.secondary_monitor (in {source_path})")
    return LayoutConfig(
        primary_monitor=int(primary_monitor),
        secondary_monitor=int(secondary_monitor),
        main_top_ratio=_require_typed_value(layout_raw, "main_top_ratio", source_path, "config.layout", float),
        landscape_width_ratio=_require_typed_value(layout_raw, "landscape_width_ratio", source_path, "config.layout", float),
    )


def _load_audio_companion_config(audio_raw: dict[str, Any], source_path: Path) -> AudioCompanionConfig:
    return AudioCompanionConfig(
        host=_require_typed_value(audio_raw, "host", source_path, "config.audio_companion", str),
        port=_require_typed_value(audio_raw, "port", source_path, "config.audio_companion", int),
    )


def _load_random_favs_browser_config(
    browser_raw: dict[str, Any] | None, project_dir: Path
) -> RandomFavsBrowserConfig:
    default_user_data_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"
    browser_values = browser_raw or {}
    return RandomFavsBrowserConfig(
        enabled=bool(browser_values.get("enabled", False)),
        shortcut_path=_resolve_path(project_dir, str(browser_values.get("shortcut_path", "Blair Chrome.lnk"))),
        user_data_dir=_resolve_path(project_dir, str(browser_values.get("user_data_dir", default_user_data_dir))),
        profile_name=str(browser_values.get("profile_name", "Blair")),
        open_count=int(browser_values.get("open_count", 10)),
        lazy_load=bool(browser_values.get("lazy_load", False)),
    )


def _load_regen_config(raw: dict[str, Any] | None, project_dir: Path) -> RegenConfig:
    values = raw or {}
    media_root = values.get("media_root")
    metadata_root = values.get("metadata_root")
    return RegenConfig(
        generate_video_url=str(values.get("generate_video_url", "https://example.com/video")),
        generate_image_url=str(values.get("generate_image_url", "https://example.com/create")),
        media_root=_resolve_path(project_dir, str(media_root)) if media_root else None,
        metadata_root=_resolve_path(project_dir, str(metadata_root)) if metadata_root else None,
    )


def _load_voice_control_config(voice_raw: dict[str, Any] | None) -> VoiceControlConfig:
    values = voice_raw or {}
    return VoiceControlConfig(
        enabled=bool(values.get("enabled", False)),
        model_path=str(values.get("model_path", "vosk-model-small-en-us-0.15")),
        device_name=str(values["device_name"]) if values.get("device_name") is not None else None,
        sample_rate=int(values.get("sample_rate", 16000)),
        confidence_threshold=float(values.get("confidence_threshold", 0.7)),
    )


def _load_vr_config(raw: dict[str, Any] | None, project_dir: Path) -> VrConfig:
    values = raw or {}
    library_dirs_raw = values.get("library_dirs", [])
    if not isinstance(library_dirs_raw, list):
        raise TypeError("vr.library_dirs must be a list of folder paths")
    audio_device = values.get("audio_device")
    return VrConfig(
        library_dirs=tuple(_resolve_path(project_dir, str(value)) for value in library_dirs_raw),
        audio_device=str(audio_device) if audio_device else None,
        tcode_udp_host=str(values.get("tcode_udp_host", "127.0.0.1")),
        tcode_udp_port=int(values.get("tcode_udp_port", 50557)),
        compositor_layers=bool(values.get("compositor_layers", False)),
    )


def _raise_for_missing_config(path: Path) -> None:
    """Fail a missing config clearly, seeding the default one from the example.

    A missing *default* ``fun_time_config.json`` is usually a fresh/public
    checkout or an overlay that got swept away, so a starter copy is written
    from the committed ``fun_time_config.example.json`` for the user to fill in.
    Startup still stops — the example's paths are placeholders that must never be
    mistaken for a real library — but with a message naming the exact file to
    edit, not an opaque ``FileNotFoundError`` deep in startup.  Any other
    explicitly-named path is simply reported as missing, without regeneration.
    """
    if path == DEFAULT_CONFIG_PATH and EXAMPLE_CONFIG_PATH.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EXAMPLE_CONFIG_PATH, path)
        raise FileNotFoundError(
            f"No config found, so a starter copy was written from "
            f"{EXAMPLE_CONFIG_PATH.name} to {path}. Fill in your real paths "
            f"(library folders, python_exe, ...) and run again."
        )
    raise FileNotFoundError(
        f"Config file not found: {path}. Copy {EXAMPLE_CONFIG_PATH.name} to it "
        f"and fill in your real paths."
    )


def load_config(config_path: str | Path | None = None, *, project_dir: Path | None = None) -> ProjectConfig:
    """Load a config, resolving its relative paths against *project_dir*.

    *project_dir* defaults to :data:`PROJECT_DIR`, the checkout that imported
    this package, which is what every session wants: a config's relative values
    describe the checkout it belongs to.  It is a parameter so that code holding
    *two* checkouts at once can say which — the branch-verification launcher
    reads the live config while running from the main player, and must resolve it
    the way the live session does rather than the way its caller happens to sit.
    """
    project_dir = project_dir or PROJECT_DIR
    path = _resolve_config_path(config_path, project_dir)
    if not path.exists():
        _raise_for_missing_config(path)
    with path.open("r", encoding="utf-8") as fp:
        raw: dict[str, Any] = json.load(fp)

    paths_raw = _require_dict(raw, "paths", path)
    layout_raw = _require_dict(raw, "layout", path)
    audio_raw = _require_dict(raw, "audio_companion", path)
    browser_raw = _require_optional_dict(raw, "random_favs_browser", path)
    if browser_raw is None:
        browser_raw = _require_optional_dict(raw, "chrome_overlay", path)
    voice_raw = _require_optional_dict(raw, "voice_control", path)
    regen_raw = _require_optional_dict(raw, "regen", path)
    vr_raw = _require_optional_dict(raw, "vr", path)

    return ProjectConfig(
        project_dir=project_dir,
        config_path=path,
        paths=_load_paths_config(paths_raw, path, project_dir),
        layout=_load_layout_config(layout_raw, path),
        audio_companion=_load_audio_companion_config(audio_raw, path),
        random_favs_browser=_load_random_favs_browser_config(browser_raw, project_dir),
        voice_control=_load_voice_control_config(voice_raw),
        regen=_load_regen_config(regen_raw, project_dir),
        loopback_port=int(raw.get("loopback_port", LOOPBACK_PORT)),
        vr=_load_vr_config(vr_raw, project_dir),
        instance_id_override=str(raw["instance_id"]) if raw.get("instance_id") else None,
    )


def _load_dir_list(
    paths_raw: dict[str, Any], list_key: str, single_key: str, source_path: Path, project_dir: Path
) -> tuple[Path, ...]:
    values = paths_raw.get(list_key)
    if values is None:
        return (_resolve_path(project_dir, str(_require_value(paths_raw, single_key, source_path, "config.paths"))),)
    if not isinstance(values, list):
        raise TypeError(f"paths.{list_key} must be a list of folder paths")
    if not values:
        raise ValueError(f"paths.{list_key} must include at least one folder path")
    return tuple(_resolve_path(project_dir, str(value)) for value in values)
