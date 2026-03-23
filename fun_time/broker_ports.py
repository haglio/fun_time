from __future__ import annotations

import re
from pathlib import Path


RE_COM0COM_PORT = re.compile(r"COM0COM\\PORT\\(CNC[AB])(\d+)", re.IGNORECASE)


def iter_serial_ports():
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    return list(list_ports.comports())


def mfp_config_path(config) -> Path:
    return config.paths.mfp_exe.with_name("MultiFunPlayer.config.json")


def read_mfp_selected_serial_port(config) -> str | None:
    try:
        config_path = mfp_config_path(config)
        if not config_path.exists():
            return None
        text = config_path.read_text(encoding="utf-8")
    except Exception:
        return None

    match = re.search(r'"SelectedSerialPort"\s*:\s*"([^"]+)"', text)
    if not match:
        return None
    return match.group(1)


def collect_com0com_ports() -> dict[str, tuple[str, str]]:
    ports: dict[str, tuple[str, str]] = {}
    for port in iter_serial_ports():
        device = getattr(port, "device", None)
        if not device:
            continue
        desc = str(getattr(port, "description", "") or "")
        hwid = str(getattr(port, "hwid", "") or "")
        if "com0com" not in desc.lower() and "COM0COM\\PORT\\" not in hwid.upper():
            continue
        ports[str(device).upper()] = (desc, hwid)
    return ports


def resolve_virtual_port(config, configured_port: str, logger) -> str:
    normalized = configured_port.upper()
    com0com_ports = collect_com0com_ports()
    if normalized in com0com_ports:
        return configured_port

    if not com0com_ports:
        logger.warning("Configured virtual port %s is missing and no com0com ports were detected", configured_port)
        return configured_port

    mfp_selected = read_mfp_selected_serial_port(config)
    if mfp_selected:
        match = RE_COM0COM_PORT.search(mfp_selected)
        if match:
            expected_role = "CNCB" if match.group(1).upper() == "CNCA" else "CNCA"
            expected_index = match.group(2)
            for device, (_desc, hwid) in com0com_ports.items():
                hwid_match = RE_COM0COM_PORT.search(hwid)
                if hwid_match and hwid_match.group(1).upper() == expected_role and hwid_match.group(2) == expected_index:
                    logger.warning(
                        "Configured virtual port %s is missing; using %s inferred from MFP serial port %s",
                        configured_port,
                        device,
                        mfp_selected,
                    )
                    return device

    cncb_devices: list[str] = []
    for device, (_desc, hwid) in com0com_ports.items():
        hwid_match = RE_COM0COM_PORT.search(hwid)
        if hwid_match and hwid_match.group(1).upper() == "CNCB":
            cncb_devices.append(device)

    if len(cncb_devices) == 1:
        logger.warning(
            "Configured virtual port %s is missing; using sole detected com0com broker-side port %s",
            configured_port,
            cncb_devices[0],
        )
        return cncb_devices[0]

    logger.warning(
        "Configured virtual port %s is missing; detected com0com ports=%s",
        configured_port,
        ", ".join(sorted(com0com_ports)),
    )
    return configured_port
