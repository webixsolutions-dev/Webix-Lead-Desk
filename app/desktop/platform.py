"""Windows-only credential protection, single-instance lock and sign-in startup."""

import ctypes
import os
import sys
from contextlib import suppress
from pathlib import Path

MUTEX_NAME = "Local\\WebixLeadDeskDesktop"
STARTUP_NAME = "WebixLeadDeskWorker"


class Blob(ctypes.Structure):
    """Windows DATA_BLOB with a pointer-sized buffer address."""

    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.c_void_p)]


def protect(data: bytes, *, decrypt: bool = False) -> bytes:
    """Encrypt for the current Windows user; never fall back to plaintext."""
    if sys.platform != "win32":
        raise RuntimeError("Windows credential protection is required")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.c_void_p))
    target = Blob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(Blob),
    ]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("Windows could not unlock or protect settings")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def data_directory() -> Path:
    """Keep private state outside the installed application and source checkout."""
    if sys.platform != "win32" or not os.environ.get("LOCALAPPDATA"):
        raise RuntimeError("Use the Windows desktop installer")
    return Path(os.environ["LOCALAPPDATA"]) / "WebixLeadDesk"


def acquire_instance() -> int:
    """Hold a Windows mutex until process exit, also respected by Setup."""
    if sys.platform != "win32":
        raise RuntimeError("Windows is required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
    if not handle or ctypes.get_last_error() == 183:
        if handle:
            release_instance(int(handle))
        raise RuntimeError("Webix Lead Desk is already open. Check the taskbar.")
    return int(handle)


def release_instance(handle: int) -> None:
    """Release the process mutex after every worker has stopped."""
    if sys.platform != "win32":
        raise RuntimeError("Windows is required")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    kernel.CloseHandle(handle)


def set_startup(enabled: bool) -> None:
    """Opt in to current-user sign-in startup without admin or stored OS passwords."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise RuntimeError("Sign-in startup requires the installed Windows application")
    import winreg

    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"
    ) as key:
        if enabled:
            winreg.SetValueEx(
                key, STARTUP_NAME, 0, winreg.REG_SZ, f'"{sys.executable}" --autostart'
            )
        else:
            with suppress(FileNotFoundError):
                winreg.DeleteValue(key, STARTUP_NAME)
