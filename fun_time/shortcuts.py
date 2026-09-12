"""Windows .lnk shortcuts, read and written in one place.

Two callers need them and each had grown its own: the branch launcher writes one
per worktree and prunes the stale ones, and the startup sequencer resolves the
one the Random Favs Browser launches from.  Both drove ``WScript.Shell``, with
their own quoting and their own way of shelling out.

There is no pure-Python way to author a shortcut.  COM is the direct route, but
pywin32 is an optional extra here, so every call falls back to driving the same
object through ``powershell.exe``.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("windows_bridge")

# What the scan puts between a shortcut's fields; none may contain it.
FIELD_SEPARATOR = "\t"


@dataclass(frozen=True)
class Shortcut:
    """What a .lnk says: what it runs, from where, with what."""
    target: str = ""
    work_dir: str = ""
    arguments: str = ""


def ps_quote(value: str) -> str:
    """*value* as a PowerShell single-quoted literal."""
    return "'" + value.replace("'", "''") + "'"


def run_powershell(script: str, *, check: bool = True) -> str:
    """*script*'s standard output, run without a profile."""
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=check,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout


def read_shortcuts(folder: Path, *, pattern: str) -> dict[Path, Shortcut]:
    """Every shortcut in *folder* matching *pattern*, by path.  One invocation
    for the whole folder: starting PowerShell costs more than reading one."""
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"Get-ChildItem -LiteralPath {ps_quote(str(folder))} "
        f"-Filter {ps_quote(pattern)} -ErrorAction SilentlyContinue "
        "| ForEach-Object { $link = $shell.CreateShortcut($_.FullName); "
        "Write-Output ($_.FullName + \"`t\" + $link.TargetPath + \"`t\" "
        "+ $link.WorkingDirectory + \"`t\" + $link.Arguments) }"
    )
    found: dict[Path, Shortcut] = {}
    for line in run_powershell(script).splitlines():
        fields = line.split(FIELD_SEPARATOR)
        if len(fields) == 4:
            found[Path(fields[0])] = Shortcut(fields[1], fields[2], fields[3])
    return found


def write_shortcut(
    destination: Path, *, target: str, arguments: str, working_dir: str,
    icon: str, description: str,
) -> None:
    """Write a .lnk at *destination*."""
    fields = {
        "TargetPath": target,
        "Arguments": arguments,
        "WorkingDirectory": working_dir,
        "IconLocation": icon,
        "Description": description,
    }
    assignments = "".join(f"$link.{name} = {ps_quote(value)}; " for name, value in fields.items())
    run_powershell(
        f"$link = (New-Object -ComObject WScript.Shell).CreateShortcut({ps_quote(str(destination))}); "
        f"{assignments}$link.Save()"
    )


def resolve_shortcut(shortcut_path: str) -> Shortcut:
    """One shortcut, through COM where pywin32 is installed.  An empty one when
    neither route answers, which the caller reads as "nothing to launch"."""
    try:
        import win32com.client  # type: ignore[import-untyped]  # noqa: PLC0415
        link = win32com.client.Dispatch("WScript.Shell").CreateShortcut(shortcut_path)
        return Shortcut(link.TargetPath, link.WorkingDirectory, link.Arguments)
    except Exception:  # noqa: BLE001 - pywin32 raises com_error, a bare Exception
        # Not narrowed on purpose: pywintypes.com_error derives straight from
        # Exception, so catching ImportError alone would send the COM failure
        # this fallback exists for straight past it.
        logger.debug("Shortcut COM resolver failed for %s", shortcut_path, exc_info=True)

    try:
        lines = run_powershell(
            f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut({ps_quote(shortcut_path)}); "
            "Write-Output $s.TargetPath; Write-Output $s.WorkingDirectory; "
            "Write-Output $s.Arguments",
            check=False,
        ).splitlines()
        if lines:
            return Shortcut(*(lines + ["", "", ""])[:3])
    except (OSError, subprocess.SubprocessError):
        logger.debug("Shortcut PowerShell resolver failed for %s", shortcut_path, exc_info=True)

    return Shortcut()
