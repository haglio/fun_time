"""Genau's folders, made up for a test, laid out the way every app finds them,
and the settings all of Haglio shares that say where they are."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from app_support import haglio_settings
from app_support.flick_folder import flat_flicks_in

from fun_time.config import ProjectConfig

SETTINGS_NAME = "haglio_settings.json"


def genau_audio_beside(genau: Path) -> Path:
    return genau.parent / "genau_audio"


def made_up_genau(root: Path) -> Path:
    genau = root / "genau"
    flat_flicks_in(genau / "flicks").mkdir(parents=True, exist_ok=True)
    genau_audio_beside(genau).mkdir(parents=True, exist_ok=True)
    return genau


def with_made_up_genau(config: ProjectConfig, root: Path) -> ProjectConfig:
    genau = made_up_genau(root)
    return replace(config, paths=replace(
        config.paths, genau_dir=genau, genau_audio_dir=genau_audio_beside(genau)))


def write_settings_naming_genau(folder: Path, genau: Path, audio: Path) -> Path:
    settings = folder / SETTINGS_NAME
    settings.write_text(json.dumps({
        **haglio_settings.EXAMPLE, "genau_dir": str(genau), "genau_audio_dir": str(audio),
    }), encoding="utf-8")
    return settings


def point_the_shared_settings_at(monkeypatch, genau: Path, audio: Path | None = None) -> None:
    settings = write_settings_naming_genau(genau.parent, genau, audio or genau_audio_beside(genau))
    monkeypatch.setenv(haglio_settings.SETTINGS_ENV, str(settings))
