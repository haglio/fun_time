"""The AppUserModelID this session's processes and its shortcut carry.

Windows groups a running window under a pinned taskbar shortcut only when the
two agree on one: the process claims it before opening any window, and the
shortcut carries the same string in its ``System.AppUserModel.ID``.  One
shell32 export, and the COM ceremony below.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import uuid

from fun_time.win32_loader import load_dll

_ole32 = load_dll("ole32")
_shell32 = load_dll("shell32")

# AppUserModelID — must match the value set on the pinned taskbar shortcut.
APP_USER_MODEL_ID = "FunTime.App"


def set_app_user_model_id(app_id: str) -> None:
    """Set the AppUserModelID for the current process.

    This must be called before any windows are created so the taskbar can
    group the process's windows with the matching pinned shortcut.
    """
    hr = _shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    if hr < 0:  # FAILED() macro
        raise OSError(f"SetCurrentProcessExplicitAppUserModelID failed: HRESULT 0x{hr:08x}")


COINIT_APARTMENTTHREADED = 0x2

# IUnknown vtable index, for _release below.
_VTBL_RELEASE = 2
CLSCTX_ALL = 0x17


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _make_guid(s: str) -> GUID:
    u = uuid.UUID(s)
    return GUID(u.time_low, u.time_mid, u.time_hi_version,
                (ctypes.c_ubyte * 8)(*u.bytes[8:]))



CLSID_ShellLink = _make_guid("00021401-0000-0000-C000-000000000046")
IID_IShellLinkW = _make_guid("000214F9-0000-0000-C000-000000000046")
IID_IPersistFile = _make_guid("0000010B-0000-0000-C000-000000000046")
IID_IPropertyStore = _make_guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]


PKEY_AppUserModel_ID = PROPERTYKEY(
    _make_guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5
)

VT_LPWSTR = 31


class PROPVARIANT(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("pwszVal", ctypes.wintypes.LPWSTR),
        ("_pad", ctypes.c_void_p),
    ]


STGM_READWRITE = 0x00000002

# IPersistFile vtable indices (IUnknown=0..2 + IPersist::GetClassID=3)
_VTBL_IPF_LOAD = 5
_VTBL_IPF_SAVE = 6

# IPropertyStore vtable indices (IUnknown=0..2)
_VTBL_IPS_GET_VALUE = 5
_VTBL_IPS_SET_VALUE = 6
_VTBL_IPS_COMMIT = 7

# IUnknown
_VTBL_QI = 0


def _query_interface(obj_addr: int, iid: GUID) -> int:
    """QueryInterface on a COM object. Returns the new interface pointer or raises."""
    out = ctypes.c_void_p()
    hr = _vtbl_call(obj_addr, _VTBL_QI, ctypes.HRESULT,
                    ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
        obj_addr, ctypes.byref(iid), ctypes.byref(out))
    if hr < 0:  # FAILED() macro
        raise OSError(f"QueryInterface failed: HRESULT 0x{hr:08x}")
    return out.value


def set_shortcut_app_user_model_id(lnk_path: str, app_id: str) -> None:
    """Set the AppUserModelID property on a .lnk shortcut file.

    Uses COM (IShellLink → IPersistFile → IPropertyStore) to write the
    System.AppUserModel.ID property, which Windows uses to match a running
    process's windows with a pinned taskbar shortcut.
    """
    _enter_apartment()
    try:
        _set_lnk_aumid(lnk_path, app_id)
    finally:
        _ole32.CoUninitialize()


def _enter_apartment() -> None:
    """Take this thread's single-threaded apartment, or refuse to work in it.

    The caller owes exactly one ``CoUninitialize`` for every call that returns
    ``S_OK`` (this call opened the apartment) or ``S_FALSE`` (the thread already
    had one, and this call still took a reference), and none at all for a call
    that failed.  The failure that reaches this path is ``RPC_E_CHANGED_MODE``:
    something else put this thread in the other concurrency model first.
    Balancing that with a ``CoUninitialize`` anyway would decrement *their*
    reference count, and the apartment they hold objects in can close under
    them; the shortcut work would also be asking for a shell link with no
    apartment of its own.  So a failed init raises before either happens.
    """
    hr = _ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    if hr < 0:
        raise OSError(f"CoInitializeEx failed: HRESULT 0x{hr & 0xFFFFFFFF:08x}")


def _set_lnk_aumid(lnk_path: str, app_id: str) -> None:
    # Create IShellLink instance
    shell_link = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(
        ctypes.byref(CLSID_ShellLink), None, CLSCTX_ALL,
        ctypes.byref(IID_IShellLinkW), ctypes.byref(shell_link),
    )
    if hr < 0:
        raise OSError(f"CoCreateInstance(ShellLink) failed: HRESULT 0x{hr:08x}")
    try:
        # Get IPersistFile and load the .lnk
        persist_file = _query_interface(shell_link.value, IID_IPersistFile)
        try:
            hr = _vtbl_call(persist_file, _VTBL_IPF_LOAD,
                            ctypes.HRESULT, ctypes.wintypes.LPCWSTR, ctypes.c_ulong)(
                persist_file, lnk_path, STGM_READWRITE)
            if hr < 0:
                raise OSError(f"IPersistFile::Load failed: HRESULT 0x{hr:08x}")

            # Get IPropertyStore and set the AUMID
            prop_store = _query_interface(shell_link.value, IID_IPropertyStore)
            try:
                pv = PROPVARIANT()
                pv.vt = VT_LPWSTR
                pv.pwszVal = app_id

                hr = _vtbl_call(prop_store, _VTBL_IPS_SET_VALUE,
                                ctypes.HRESULT,
                                ctypes.POINTER(PROPERTYKEY),
                                ctypes.POINTER(PROPVARIANT))(
                    prop_store,
                    ctypes.byref(PKEY_AppUserModel_ID),
                    ctypes.byref(pv))
                if hr < 0:  # FAILED() macro — S_FALSE (1) is success
                    raise OSError(f"IPropertyStore::SetValue failed: HRESULT 0x{hr:08x}")

                hr = _vtbl_call(prop_store, _VTBL_IPS_COMMIT, ctypes.HRESULT)(prop_store)
                if hr < 0:
                    raise OSError(f"IPropertyStore::Commit failed: HRESULT 0x{hr:08x}")
            finally:
                _release(prop_store)

            # Save the .lnk back to disk
            hr = _vtbl_call(persist_file, _VTBL_IPF_SAVE,
                            ctypes.HRESULT, ctypes.wintypes.LPCWSTR, ctypes.wintypes.BOOL)(
                persist_file, lnk_path, True)
            if hr < 0:
                raise OSError(f"IPersistFile::Save failed: HRESULT 0x{hr:08x}")
        finally:
            _release(persist_file)
    finally:
        _release(shell_link.value)


def _read_shortcut_app_user_model_id(lnk_path: str) -> str | None:
    """Read the AppUserModelID property from a .lnk file (for testing)."""
    _enter_apartment()
    try:
        return _get_lnk_aumid(lnk_path)
    finally:
        _ole32.CoUninitialize()


def _get_lnk_aumid(lnk_path: str) -> str | None:
    shell_link = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(
        ctypes.byref(CLSID_ShellLink), None, CLSCTX_ALL,
        ctypes.byref(IID_IShellLinkW), ctypes.byref(shell_link),
    )
    if hr != 0:
        return None
    try:
        persist_file = _query_interface(shell_link.value, IID_IPersistFile)
        try:
            hr = _vtbl_call(persist_file, _VTBL_IPF_LOAD,
                            ctypes.HRESULT, ctypes.wintypes.LPCWSTR, ctypes.c_ulong)(
                persist_file, lnk_path, 0)  # STGM_READ = 0
            if hr != 0:
                return None

            prop_store = _query_interface(shell_link.value, IID_IPropertyStore)
            try:
                pv = PROPVARIANT()
                hr = _vtbl_call(prop_store, _VTBL_IPS_GET_VALUE,
                                ctypes.HRESULT,
                                ctypes.POINTER(PROPERTYKEY),
                                ctypes.POINTER(PROPVARIANT))(
                    prop_store,
                    ctypes.byref(PKEY_AppUserModel_ID),
                    ctypes.byref(pv))
                if hr != 0 or pv.vt != VT_LPWSTR:
                    return None
                return pv.pwszVal
            finally:
                _release(prop_store)
        finally:
            _release(persist_file)
    finally:
        _release(shell_link.value)


def _vtbl_call(obj_addr: int, index: int, restype: type, *argtypes: type):
    """Build a callable for COM vtable method at *index*. Caller passes 'this' as first arg."""
    vtbl = ctypes.c_void_p.from_address(obj_addr).value
    func_ptr = ctypes.c_void_p.from_address(
        vtbl + index * ctypes.sizeof(ctypes.c_void_p)
    ).value
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(func_ptr)


def _release(obj_addr: int) -> None:
    _vtbl_call(obj_addr, _VTBL_RELEASE, ctypes.c_ulong)(obj_addr)
