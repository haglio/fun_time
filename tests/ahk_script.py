from __future__ import annotations

from pathlib import Path

THE_RESIDENT_ENTRYPOINT_NO_TEST_CAN_IMPORT = Path(__file__).resolve().parents[1] / "windows_bridge_hotkeys.ahk"


def script_text() -> str:
    return THE_RESIDENT_ENTRYPOINT_NO_TEST_CAN_IMPORT.read_text(encoding="utf-8")


def suspend_exempt_block() -> str:
    text = script_text()
    return text.split("#SuspendExempt true", 1)[1].split("#SuspendExempt false", 1)[0]


def suspend_exempt_keys() -> set[str]:
    return {line.split("::", 1)[0] for line in suspend_exempt_block().splitlines() if "::" in line}


def function_source(name: str) -> str:
    text = script_text()
    for line in text.splitlines():
        if line.startswith(f"{name}(") and line.endswith("{"):
            start = text.index(f"\n{line}\n")
            return text[start:text.index("\n}\n", start) + len("\n}\n")].strip()
    raise AssertionError(f"the script defines no function named {name}")
