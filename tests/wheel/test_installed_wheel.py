"""Checks that only make sense against an installed wheel.

Run with ``python -I`` from a clean virtual environment that has the wheel
installed and no source tree, no UKIRYU_CDYLIB and no PYTHONPATH. They prove
the package that loaded is the installed one, the library came from inside
it, and that library is byte-for-byte the one the build recorded.

UKIRYU_WHEEL_EVIDENCE names the JSON written by scripts/check_wheel.py.
"""

import hashlib
import json
import os
import sysconfig
import unittest
from pathlib import Path

import ukiryu


class InstalledWheel(unittest.TestCase):
    def test_no_override_is_in_play(self):
        self.assertNotIn("UKIRYU_CDYLIB", os.environ)

    def test_package_is_the_installed_one(self):
        site = Path(sysconfig.get_paths()["purelib"]).resolve()
        package = Path(ukiryu.__file__).resolve()
        self.assertIn(site, package.parents, "%s is not under %s" % (package, site))

    def test_library_is_the_packaged_one(self):
        info = ukiryu.library_info()
        self.assertEqual(info.source, "packaged")
        self.assertEqual(info.abi_version, 1)
        self.assertIn(Path(ukiryu.__file__).resolve().parent, Path(info.path).resolve().parents)

    def test_library_matches_the_recorded_build(self):
        path = os.environ.get("UKIRYU_WHEEL_EVIDENCE")
        if not path:
            raise AssertionError("UKIRYU_WHEEL_EVIDENCE must be set")
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        loaded = hashlib.sha256(Path(ukiryu.library_info().path).read_bytes()).hexdigest()
        self.assertEqual(loaded, record["library_sha256"])

    def test_core_answers_over_the_wire(self):
        self.assertTrue(ukiryu.core_version().version)


if __name__ == "__main__":
    unittest.main()
