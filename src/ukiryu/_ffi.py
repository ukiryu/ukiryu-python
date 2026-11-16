"""ctypes loader and call boundary for the ukiryu-ffi C ABI.

The C surface (``ukiryu-rs/ukiryu-ffi/include/ukiryu.h``) is three
functions::

    uint32_t ukiryu_abi_version(void);
    char *ukiryu_json(const uint8_t *input, size_t length);
    void ukiryu_free(char *reply);

``ukiryu_json`` borrows the request bytes for the call and returns an owned,
NUL-terminated UTF-8 JSON reply that must be released exactly once with
``ukiryu_free``. The reply pointer is therefore typed ``c_void_p`` (a
``c_char_p`` result would be copied and the pointer lost), copied with
``ctypes.string_at`` and freed in a ``finally`` block.

Search order, first match wins:

1. ``UKIRYU_CDYLIB``: an explicit path. When it is set, nothing else is
   tried; a failure to load it is an error, never a silent fallback.
2. The packaged library, ``ukiryu/lib/<name>`` next to this module. When
   the file exists it must load; only an absent file moves on.
3. The system loader: ``ctypes.util.find_library("ukiryu_ffi")``, then the
   bare file name through the platform's dynamic loader search path.

A failure lists every candidate tried and why it was rejected.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

from .errors import LibraryLoadError, ProtocolError

ABI_VERSION = 1
ENV_VAR = "UKIRYU_CDYLIB"


def library_file_name(platform: Optional[str] = None) -> str:
    """The file name cargo gives the ukiryu-ffi cdylib on a platform."""
    platform = platform or sys.platform
    if platform.startswith("win") or platform == "cygwin":
        return "ukiryu_ffi.dll"
    if platform == "darwin":
        return "libukiryu_ffi.dylib"
    return "libukiryu_ffi.so"


def packaged_library_path() -> Path:
    return Path(__file__).resolve().parent / "lib" / library_file_name()


@dataclass(frozen=True)
class LibraryInfo:
    """Which library was loaded and how it was found."""

    source: str  # "env", "packaged" or "system"
    path: str
    abi_version: int


class _Library:
    def __init__(self, handle: ctypes.CDLL, info: LibraryInfo) -> None:
        self.handle = handle
        self.info = info

    def call(self, request: Any) -> Any:
        payload = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
        reply = self.handle.ukiryu_json(payload, len(payload))
        if not reply:
            raise ProtocolError("ukiryu_json returned a null reply")
        try:
            raw = ctypes.string_at(reply)
        finally:
            self.handle.ukiryu_free(reply)
        try:
            return json.loads(raw.decode("utf-8"), parse_float=_reject_float)
        except (UnicodeDecodeError, ValueError) as error:
            raise ProtocolError("reply is not wire JSON: %s" % error) from None


def _reject_float(text: str) -> Any:
    # Every Float crosses as {"$float": ...}; a bare float would be lossy.
    raise ValueError("bare JSON float %s in a reply" % text)


def _bind(handle: ctypes.CDLL) -> None:
    handle.ukiryu_abi_version.argtypes = []
    handle.ukiryu_abi_version.restype = ctypes.c_uint32
    handle.ukiryu_json.argtypes = [ctypes.c_char_p, ctypes.c_size_t]
    handle.ukiryu_json.restype = ctypes.c_void_p
    handle.ukiryu_free.argtypes = [ctypes.c_void_p]
    handle.ukiryu_free.restype = None


def _open(source: str, path: str, attempts: List[Tuple[str, str, str]]) -> _Library:
    try:
        handle = ctypes.CDLL(path)
    except OSError as error:
        attempts.append((source, path, "load failed: %s" % error))
        raise LibraryLoadError(_message(attempts), attempts) from None
    try:
        _bind(handle)
    except AttributeError as error:
        attempts.append((source, path, "not the ukiryu C ABI: %s" % error))
        raise LibraryLoadError(_message(attempts), attempts) from None
    abi = handle.ukiryu_abi_version()
    if abi != ABI_VERSION:
        attempts.append((source, path, "ABI version %d, this binding needs %d" % (abi, ABI_VERSION)))
        raise LibraryLoadError(_message(attempts), attempts)
    return _Library(handle, LibraryInfo(source, path, abi))


def _message(attempts: List[Tuple[str, str, str]]) -> str:
    lines = ["cannot load the ukiryu-ffi library. Tried, in order:"]
    lines += ["  [%s] %s: %s" % attempt for attempt in attempts]
    lines.append("Set %s to the built library (cargo build -p ukiryu-ffi --release "
                 "in the ukiryu-rs submodule) or install a wheel that vendors it." % ENV_VAR)
    return "\n".join(lines)


def load_library() -> _Library:
    """Load the library following the documented search order."""
    attempts: List[Tuple[str, str, str]] = []
    explicit = os.environ.get(ENV_VAR)
    if explicit:
        return _open("env", explicit, attempts)
    attempts.append(("env", ENV_VAR, "not set"))
    packaged = packaged_library_path()
    if packaged.exists():
        return _open("packaged", str(packaged), attempts)
    attempts.append(("packaged", str(packaged), "not present"))
    found = ctypes.util.find_library("ukiryu_ffi")
    if found:
        return _open("system", found, attempts)
    attempts.append(("system", "find_library('ukiryu_ffi')", "not found"))
    return _open("system", library_file_name(), attempts)


_lock = threading.Lock()
_loaded: Optional[_Library] = None


def library() -> _Library:
    global _loaded
    if _loaded is None:
        with _lock:
            if _loaded is None:
                _loaded = load_library()
    return _loaded
