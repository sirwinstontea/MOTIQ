"""Keeps the xAI key encrypted at rest. On Windows it uses DPAPI (CryptProtectData, current user scope): the
ciphertext in .motiq/config.json can only be decrypted by the same Windows user on the same PC. Elsewhere it falls
back to base64 in a file only the user can read (chmod 600), which is obfuscation, not encryption."""
from __future__ import annotations

import base64
import ctypes
import os
import sys
from ctypes import wintypes

WIN = sys.platform == "win32"
ENTROPY = b"MOTIQ launcher xAI key v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    src, ent, out = _blob(data), _blob(ENTROPY), _Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    args = (ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x01, ctypes.byref(out)) if encrypt else \
           (ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x01, ctypes.byref(out))
    if not fn(*args):                                   # 0x01 = CRYPTPROTECT_UI_FORBIDDEN
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(plain: str) -> str:
    if not plain:
        return ""
    raw = plain.encode("utf-8")
    if WIN:
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode()
    return "b64:" + base64.b64encode(raw).decode()


def unprotect(token: str) -> str:
    """'' when missing or unreadable (e.g. config copied from another user/PC): setup then asks for the key again."""
    try:
        kind, _, data = (token or "").partition(":")
        if kind == "dpapi" and WIN:
            return _dpapi(base64.b64decode(data), False).decode("utf-8")
        if kind == "b64":
            return base64.b64decode(data).decode("utf-8")
    except (OSError, ValueError):
        pass
    return ""


def restrict(path) -> None:
    """Owner-only file permissions where the OS supports them (Windows: the per-user AppData folder already is)."""
    if not WIN:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
