"""Library search order, diagnostics and reply ownership.

These tests need the built library in UKIRYU_CDYLIB. Each search-order case
runs in a fresh interpreter so that the process-wide loaded library does not
leak between cases.
"""

import ctypes
import ctypes.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ukiryu import _ffi

SRC = Path(__file__).resolve().parents[1] / "src"
PROBE = (
    "import json, ukiryu\n"
    "try:\n"
    "    info = ukiryu.library_info()\n"
    "    print(json.dumps({'source': info.source, 'path': info.path, 'abi': info.abi_version,"
    " 'core': ukiryu.core_version().version}))\n"
    "except ukiryu.LibraryLoadError as e:\n"
    "    print(json.dumps({'error': e.code, 'attempts': [list(a) for a in e.attempts],"
    " 'message': e.message}))\n"
)


def built_library() -> str:
    path = os.environ.get("UKIRYU_CDYLIB")
    if not path or not Path(path).is_file():
        raise AssertionError("UKIRYU_CDYLIB must name the built ukiryu-ffi library")
    return path


def probe(env_overrides, pythonpath):
    env = {k: v for k, v in os.environ.items()
           if k not in ("UKIRYU_CDYLIB", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")}
    env.update(env_overrides)
    env["PYTHONPATH"] = str(pythonpath)
    out = subprocess.run([sys.executable, "-c", PROBE], env=env, check=True,
                         capture_output=True, text=True).stdout
    return json.loads(out)


def foreign_library() -> str:
    if sys.platform.startswith("win"):
        return "kernel32.dll"
    found = ctypes.util.find_library("c")
    if not found:
        raise AssertionError("no C library to use as a foreign library")
    return found


class SearchOrder(unittest.TestCase):
    def test_env_path_is_used_first(self):
        result = probe({"UKIRYU_CDYLIB": built_library()}, SRC)
        self.assertEqual(result["source"], "env")
        self.assertEqual(result["path"], built_library())
        self.assertEqual(result["abi"], 1)

    def test_a_broken_env_path_never_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = self._package_copy(tmp)  # a packaged library exists too
            missing = str(Path(tmp) / "missing" / _ffi.library_file_name())
            result = probe({"UKIRYU_CDYLIB": missing}, package)
        self.assertEqual(result["error"], "LIBRARY_LOAD_ERROR")
        self.assertEqual(len(result["attempts"]), 1)
        source, path, reason = result["attempts"][0]
        self.assertEqual((source, path), ("env", missing))
        self.assertTrue(reason.startswith("load failed: "), reason)
        self.assertIn("UKIRYU_CDYLIB", result["message"])

    def test_packaged_library_is_used_without_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = self._package_copy(tmp)
            result = probe({}, package)
            expected = str(Path(package, "ukiryu", "lib", _ffi.library_file_name()).resolve())
        self.assertEqual(result["source"], "packaged")
        self.assertEqual(result["path"], expected)

    def test_without_env_or_package_the_system_loader_is_tried_and_reported(self):
        result = probe({}, SRC)
        self.assertEqual(result["error"], "LIBRARY_LOAD_ERROR")
        sources = [attempt[0] for attempt in result["attempts"]]
        self.assertEqual(sources, ["env", "packaged", "system", "system"])
        self.assertEqual(result["attempts"][0][2], "not set")
        self.assertEqual(result["attempts"][1][2], "not present")
        self.assertEqual(result["attempts"][2], ["system", "find_library('ukiryu_ffi')", "not found"])
        self.assertEqual(result["attempts"][3][1], _ffi.library_file_name())

    def test_a_library_without_the_abi_is_rejected(self):
        result = probe({"UKIRYU_CDYLIB": foreign_library()}, SRC)
        self.assertEqual(result["error"], "LIBRARY_LOAD_ERROR")
        self.assertIn("not the ukiryu C ABI", result["attempts"][-1][2])

    def _package_copy(self, tmp) -> Path:
        root = Path(tmp) / "site"
        shutil.copytree(SRC / "ukiryu", root / "ukiryu",
                        ignore=shutil.ignore_patterns("__pycache__", "lib"))
        (root / "ukiryu" / "lib").mkdir()
        shutil.copy2(built_library(), root / "ukiryu" / "lib" / _ffi.library_file_name())
        return root


class Ownership(unittest.TestCase):
    def setUp(self):
        self.lib = _ffi.library()
        self.handle = self.lib.handle

    def test_reply_is_copied_then_freed_once(self):
        payload = b'{"op":"version"}'
        for _ in range(2000):
            pointer = self.handle.ukiryu_json(payload, len(payload))
            self.assertIsInstance(pointer, int)
            try:
                reply = json.loads(ctypes.string_at(pointer))
            finally:
                self.handle.ukiryu_free(pointer)
            self.assertIs(reply["ok"], True)
        self.handle.ukiryu_free(None)  # null is accepted

    def test_length_delimits_the_request(self):
        payload = b'{"op":"version"}trailing'
        pointer = self.handle.ukiryu_json(payload, len(b'{"op":"version"}'))
        try:
            self.assertIs(json.loads(ctypes.string_at(pointer))["ok"], True)
        finally:
            self.handle.ukiryu_free(pointer)

    def test_invalid_bytes_are_an_invalid_request(self):
        for payload in (b"", b"not json", b"\xff\xfe", b'{"op":"nope"}'):
            pointer = self.handle.ukiryu_json(payload, len(payload))
            try:
                reply = json.loads(ctypes.string_at(pointer))
            finally:
                self.handle.ukiryu_free(pointer)
            self.assertEqual(reply["error"]["code"], "INVALID_REQUEST", payload)


if __name__ == "__main__":
    unittest.main()
