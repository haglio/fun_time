from __future__ import annotations

import logging
from ctypes import POINTER, Structure, byref, c_uint, c_ushort, c_void_p, wstring_at
from ctypes.wintypes import DWORD, LPCWSTR
from dataclasses import dataclass

import comtypes
from comtypes import CLSCTX_ALL, COMMETHOD, GUID, HRESULT, IUnknown

from fun_time.win32_loader import load_dll

logger = logging.getLogger(__name__)


FLOWS = ("output", "input")  # Windows' EDataFlow, numbered in this order
ROLES = ("console", "multimedia", "communications")  # its ERole, likewise


@dataclass(frozen=True)
class SoundDevice:
    id: str
    name: str


Defaults = dict[tuple[str, str], SoundDevice]


class _PropertyKey(Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class _PropVariant(Structure):
    _fields_ = [("vt", c_ushort), ("reserved1", c_ushort), ("reserved2", c_ushort),
                ("reserved3", c_ushort), ("text", c_void_p), ("rest", c_void_p)]


_ole32 = load_dll("ole32")
_ole32.CoTaskMemFree.argtypes = [c_void_p]
_ole32.CoTaskMemFree.restype = None
_ole32.PropVariantClear.argtypes = [POINTER(_PropVariant)]
_ole32.PropVariantClear.restype = HRESULT

_VT_LPWSTR = 31
_FRIENDLY_NAME = _PropertyKey(GUID("{a45c254e-df1c-4efd-8020-67d146a850e0}"), 14)
_DEVICE_ENUMERATOR = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_POLICY_CONFIG_CLIENT = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")


class _IPropertyStore(IUnknown):
    _iid_ = GUID("{886d8eeb-8cf2-4446-8d02-cdba1dbdcf99}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(DWORD))),
        COMMETHOD([], HRESULT, "GetAt", (["in"], DWORD), (["out"], POINTER(_PropertyKey))),
        COMMETHOD([], HRESULT, "GetValue",
                  (["in"], POINTER(_PropertyKey)), (["out"], POINTER(_PropVariant))),
    ]


class _IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate", (["in"], POINTER(GUID)), (["in"], DWORD),
                  (["in"], c_void_p), (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "OpenPropertyStore",
                  (["in"], DWORD), (["out"], POINTER(POINTER(_IPropertyStore)))),
        COMMETHOD([], HRESULT, "GetId", (["out"], POINTER(c_void_p))),
    ]


class _IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                  (["in"], c_uint), (["in"], DWORD), (["out"], POINTER(c_void_p))),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                  (["in"], c_uint), (["in"], c_uint), (["out"], POINTER(POINTER(_IMMDevice)))),
    ]


_SLOTS_BEFORE_SET_DEFAULT_ENDPOINT = (
    "GetMixFormat", "GetDeviceFormat", "ResetDeviceFormat", "SetDeviceFormat",
    "GetProcessingPeriod", "SetProcessingPeriod", "GetShareMode", "SetShareMode",
    "GetPropertyValue", "SetPropertyValue",
)


class _IPolicyConfig(IUnknown):
    _iid_ = GUID("{f8679f50-850a-41cf-9c72-430f290290c8}")
    _methods_ = [
        *(COMMETHOD([], HRESULT, name) for name in _SLOTS_BEFORE_SET_DEFAULT_ENDPOINT),
        COMMETHOD([], HRESULT, "SetDefaultEndpoint", (["in"], LPCWSTR), (["in"], c_uint)),
    ]


def _com_on_this_thread() -> None:
    try:
        comtypes.CoInitialize()
    except OSError:
        pass  # already started on this thread, in the other apartment


def _id_of(device) -> str:
    address = device.GetId()
    try:
        return wstring_at(address)
    finally:
        _ole32.CoTaskMemFree(address)


def _name_of(device) -> str:
    value = device.OpenPropertyStore(0).GetValue(byref(_FRIENDLY_NAME))
    try:
        return wstring_at(value.text) if value.vt == _VT_LPWSTR and value.text else ""
    finally:
        _ole32.PropVariantClear(byref(value))


def _default_device_or_none(enumerator, flow: str, role: str) -> SoundDevice | None:
    try:
        device = enumerator.GetDefaultAudioEndpoint(FLOWS.index(flow), ROLES.index(role))
        return SoundDevice(_id_of(device), _name_of(device))
    except (OSError, comtypes.COMError):
        return None


def default_devices() -> Defaults:
    try:
        _com_on_this_thread()
        enumerator = comtypes.CoCreateInstance(
            _DEVICE_ENUMERATOR, _IMMDeviceEnumerator, CLSCTX_ALL)
    except (OSError, comtypes.COMError):
        logger.warning("Could not ask Windows for its default sound devices", exc_info=True)
        return {}
    return {(flow, role): device for flow in FLOWS for role in ROLES
            if (device := _default_device_or_none(enumerator, flow, role)) is not None}


def make_default(device: SoundDevice, role: str) -> None:
    try:
        _com_on_this_thread()
        policy = comtypes.CoCreateInstance(_POLICY_CONFIG_CLIENT, _IPolicyConfig, CLSCTX_ALL)
        policy.SetDefaultEndpoint(device.id, ROLES.index(role))
    except (OSError, comtypes.COMError):
        logger.warning("Could not make %s Windows' default for %s",
                       device.name, role, exc_info=True)
