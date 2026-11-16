"""Build one platform wheel around a built ukiryu-ffi library.

Usage: python scripts/build_wheel.py LIBRARY PLATFORM_TAG OUT_DIR

Copies LIBRARY to src/ukiryu/lib/ under the name the loader expects, builds
a wheel tagged py3-none-PLATFORM_TAG with pip (no isolation, no sdist), and
removes the copy again. Needs setuptools>=70.1 and wheel installed.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ukiryu._ffi import library_file_name  # noqa: E402


def main() -> int:
    if len(sys.argv) != 4:
        sys.stderr.write(__doc__)
        return 2
    library, plat, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    if not library.is_file():
        sys.stderr.write("library not found: %s\n" % library)
        return 2
    lib_dir = ROOT / "src" / "ukiryu" / "lib"
    shutil.rmtree(lib_dir, ignore_errors=True)
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    lib_dir.mkdir(parents=True)
    shutil.copy2(library, lib_dir / library_file_name())
    out.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([sys.executable, "-m", "pip", "wheel", str(ROOT), "--no-deps",
                        "--no-build-isolation", "-w", str(out)],
                       check=True, env=dict(os.environ, UKIRYU_WHEEL_PLAT=plat))
    finally:
        shutil.rmtree(lib_dir, ignore_errors=True)
        shutil.rmtree(ROOT / "build", ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
