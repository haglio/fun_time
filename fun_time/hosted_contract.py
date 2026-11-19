from __future__ import annotations

import json
import logging
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from funestra.file_channel import append_command
from shared_ui.message_shape import is_answering_what_he_said

if TYPE_CHECKING:
    from .bridge_records import BridgeConfig

from app_support.file_channel import write_whole
from app_support.siblings import primary_checkout_of

logger = logging.getLogger(__name__)

CONTRACT_FILE = "origenerator_contract.json"
ASKING_BUDGET_S = 30.0
SAID_AT_FIELD = "said_at_epoch_seconds"


@dataclass(frozen=True)
class HostedVocabulary:
    phrases: frozenset[str] = frozenset()
    openers: frozenset[str] = frozenset()
    speech_hint: str = ""
    lock_phrases: frozenset[str] = frozenset()
    actions_heard_as: tuple[tuple[str, tuple[str, ...]], ...] = ()


NO_HOSTED_VOCABULARY = HostedVocabulary()
ACTIONS_HEARD_AS_FILE = "labels_heard_as.json"


def publish_actions_heard_as(state_dir: Path, vocabulary: HostedVocabulary) -> None:
    write_whole(Path(state_dir) / ACTIONS_HEARD_AS_FILE, json.dumps(dict(vocabulary.actions_heard_as)))


def published_actions_heard_as(state_dir: Path) -> dict[str, tuple[str, ...]]:
    try:
        told = json.loads((Path(state_dir) / ACTIONS_HEARD_AS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {name: tuple(forms) for name, forms in told.items()}


def what_it_answers_by_voice(checkout: str | Path, *, python_exe: str | Path,
                             launch_kwargs: dict,
                             run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
                             ) -> HostedVocabulary:
    module = (published_by(checkout) or {}).get("spoken_phrases_module")
    if not module:
        logger.warning("The Origenerator at %s does not say what it answers by voice, so no "
                       "phrase of its is heard said to a satellite", checkout)
        return NO_HOSTED_VOCABULARY
    try:
        answer = json.loads(run([str(python_exe), "-m", module], capture_output=True,
                                text=True, timeout=ASKING_BUDGET_S, check=True,
                                **launch_kwargs).stdout)
        return HostedVocabulary(frozenset(answer["phrases"]), frozenset(answer["openers"]),
                                answer["speech_hint"], frozenset(answer.get("lock_phrases", ())),
                                tuple((name, tuple(forms)) for name, forms
                                      in answer.get("labels_heard_as", {}).items()))
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        logger.warning("The Origenerator at %s could not say what it answers by voice (%s), "
                       "so no phrase of its is heard said to a satellite", checkout, error)
        return NO_HOSTED_VOCABULARY


def gallery_names_file(checkout: str | Path, *,
                       primary_of: Callable[[Path], Path] | None = None) -> Path | None:
    if not checkout:
        return None
    kept_at = (published_by(checkout) or {}).get("gallery_names_file")
    if not kept_at:
        return None
    return (primary_of or primary_checkout_of)(Path(checkout)) / kept_at


def gallery_names_args(checkout: str | Path) -> list[str]:
    kept_at = gallery_names_file(checkout)
    return ["--gallery-names-file", str(kept_at)] if kept_at is not None else []


def published_by(checkout: str | Path) -> dict | None:
    document = Path(checkout) / CONTRACT_FILE
    if not document.is_file():
        return None
    return json.loads(document.read_text(encoding="utf-8"))


def declared_flags(contract: dict) -> frozenset[str]:
    by_satellite = (flag for group in ("region_flags", "player_flags", "message_flags")
               for flags in contract.get(group, {}).values() for flag in flags)
    return frozenset({*contract["required_flags"], *by_satellite, *contract.get("headset_flags", ())})


def _each_flag_with_its_values(args: list[str]) -> list[list[str]]:
    flags: list[list[str]] = []
    for word in args:
        if word.startswith("--") or not flags:
            flags.append([word])
        else:
            flags[-1].append(word)
    return flags


def the_args_it_declares(checkout: str | Path, args: list[str]) -> list[str]:
    contract = published_by(checkout)
    if contract is None:
        return args
    declared = declared_flags(contract)
    flags = _each_flag_with_its_values(args)
    if left_off := [flag for flag, *_values in flags if flag not in declared]:
        logger.warning("The Origenerator at %s does not declare %s, so it is sent its session "
                       "without them", checkout, ", ".join(left_off))
    return [word for flag in flags if flag[0] in declared for word in flag]


OPEN_SLIDESHOW = "open_slideshow"
CLOSE_SLIDESHOWS = "CLOSE_SLIDESHOWS"


def said_about(satellite: str, verb: str) -> str:
    return f"{satellite}_{verb}"


@dataclass(frozen=True)
class WhenSaid:
    said_at_suffix: str = ""
    heard_suffix: str = ""


NOT_TOLD_WHEN = WhenSaid()


def when_said_of(checkout: str | Path) -> WhenSaid:
    published = published_by(checkout) or {}
    return WhenSaid(published.get("said_at_suffix", ""), published.get("heard_suffix", ""))


def with_when_said(line: str, spoken_at: float | None, when: WhenSaid) -> str:
    return line + _the_time_it_was_said(spoken_at, when) + _that_he_said_it(line, when)


def _the_time_it_was_said(spoken_at: float | None, when: WhenSaid) -> str:
    placeholder = "{" + SAID_AT_FIELD + "}"
    if spoken_at is None or placeholder not in when.said_at_suffix:
        return ""
    said_at = time.time() - (time.monotonic() - spoken_at)
    return when.said_at_suffix.replace(placeholder, f"{said_at:.3f}")


def _that_he_said_it(line: str, when: WhenSaid) -> str:
    carries_words_or_a_file = ":" in line or "|" in line
    if carries_words_or_a_file or not is_answering_what_he_said():
        return ""
    return when.heard_suffix


def written_for_the_hosted_app(lines: list[str], config: BridgeConfig,
                               spoken_at: float | None = None) -> list:
    for line in lines:
        append_command(config.origenerator.cmd_file,
                       with_when_said(line, spoken_at, config.origenerator_when_said))
    return []


def said_to_the_hosted_app(satellite: str, words: str) -> str:
    return f"{satellite.upper()}_SAY:{words}"


def said_to_core(words: str) -> str:
    return f"SAY:{words}"



