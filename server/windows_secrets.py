"""Read the existing Windows-user DPAPI settings without exposing secrets."""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _unprotect(ciphertext: str) -> str:
    raw = bytes.fromhex(ciphertext)
    source = ctypes.create_string_buffer(raw)
    protected = DataBlob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    plain = DataBlob()
    crypt32 = ctypes.WinDLL("Crypt32.dll")
    crypt32.CryptUnprotectData.argtypes = (
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    )
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(ctypes.byref(protected), None, None, None, None, 0, ctypes.byref(plain)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(plain.data, plain.size).decode("utf-16-le")
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(plain.data, ctypes.c_void_p))


def load_telegram_settings(path: Path) -> tuple[str, str]:
    if os.name != "nt":
        raise RuntimeError("saved Windows settings require Windows")
    settings = json.loads(path.read_text(encoding="utf-8-sig"))
    if settings.get("version") != 1 or not isinstance(settings.get("secrets"), dict):
        raise ValueError("unsupported startup settings")
    secrets = settings["secrets"]
    return tuple(_unprotect(secrets[name]) for name in (
        "DASHBOARD_TELEGRAM_BOT_TOKEN", "DASHBOARD_TELEGRAM_CHAT_ID",
    ))
