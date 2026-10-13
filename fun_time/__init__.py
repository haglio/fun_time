from __future__ import annotations

from .project_paths import PROJECT_DIR, PROJECT_ICON, apply_genau_dirs_to_sys_path

apply_genau_dirs_to_sys_path()

from .config import DEFAULT_CONFIG_PATH, ProjectConfig, load_config  # noqa: E402

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "PROJECT_DIR",
    "PROJECT_ICON",
    "ProjectConfig",
    "load_config",
]
