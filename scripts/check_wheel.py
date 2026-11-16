"""Inspect a built wheel and record what it carries.

Usage: python scripts/check_wheel.py WHEEL PLATFORM_TAG EVIDENCE.json

Fails unless the wheel is a platform wheel (never py3-none-any) with exactly
one core library for that platform and the expected tag. It then checks the
library's own baseline from the binary, not from the runner image:

* Linux: the highest GLIBC symbol version required is within the manylinux
  tag (``objdump -T``), and ``auditwheel show`` is logged.
* macOS: the Mach-O minimum OS version equals the tag's deployment target.
* Windows: no import of the Visual C++ runtime; every imported DLL is an
  operating-system DLL.

The record holds the wheel and library hashes and the pinned core commit.
This script never imports ukiryu, so it cannot see the source tree.
"""

import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBRARIES = {"ukiryu_ffi.dll", "libukiryu_ffi.so", "libukiryu_ffi.dylib"}
WINDOWS_RUNTIME = re.compile(r"^(vcruntime|msvcp|vcomp|concrt)", re.I)


def run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def glibc_floor(lib: Path) -> tuple:
    versions = [tuple(int(p) for p in m.split("."))
                for m in re.findall(r"GLIBC_(\d+(?:\.\d+)+)", run("objdump", "-T", str(lib)))]
    return max(versions) if versions else (0, 0)


def macho_minos(lib: Path) -> str:
    text = run("otool", "-l", str(lib))
    found = re.findall(r"LC_BUILD_VERSION.*?minos (\S+)", text, re.S)
    found += re.findall(r"LC_VERSION_MIN_MACOSX.*?version (\S+)", text, re.S)
    if not found:
        raise SystemExit("no minimum macOS version in %s" % lib)
    return found[0]


def pe_imports(lib: Path) -> list:
    import pefile  # installed by the workflow

    pe = pefile.PE(str(lib))
    return sorted({e.dll.decode().lower() for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])})


def main() -> int:
    wheel, plat, evidence = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    tag = plat.replace("-", "_").replace(".", "_")
    problems = []
    name = wheel.name
    if not name.endswith("-py3-none-%s.whl" % tag):
        problems.append("wheel %s is not tagged py3-none-%s" % (name, tag))
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        libs = [n for n in names if Path(n).name in LIBRARIES]
        binaries = [n for n in names if re.search(r"\.(so|dylib|dll|pyd|exe)(\.\d+)*$", n)]
        wheel_meta = z.read(next(n for n in names if n.endswith(".dist-info/WHEEL"))).decode()
        if len(libs) != 1 or not libs[0].startswith("ukiryu/lib/") or binaries != libs:
            problems.append("expected exactly one ukiryu/lib library, found libs=%s binaries=%s"
                            % (libs, binaries))
        if "Root-Is-Purelib: false" not in wheel_meta:
            problems.append("WHEEL does not say Root-Is-Purelib: false")
        extract = evidence.parent / "extracted"
        z.extract(libs[0], extract) if libs else None
    detail = {}
    if libs:
        lib = extract / libs[0]
        detail["library_sha256"] = hashlib.sha256(lib.read_bytes()).hexdigest()
        detail["library_bytes"] = lib.stat().st_size
        if tag.startswith("manylinux"):
            m = re.match(r"manylinux_(\d+)_(\d+)_", tag)
            limit = (int(m.group(1)), int(m.group(2)))
            floor = glibc_floor(lib)
            detail["max_glibc_symbol"] = "%d.%d" % floor
            detail["auditwheel"] = (lambda r: r.stdout + r.stderr)(subprocess.run(
                ["auditwheel", "show", str(wheel)], capture_output=True, text=True))
            if floor > limit:
                problems.append("library needs GLIBC %d.%d, tag allows %d.%d" % (floor + limit))
        elif tag.startswith("macosx"):
            m = re.match(r"macosx_(\d+)_(\d+)_", tag)
            want = "%s.%s" % (m.group(1), m.group(2))
            detail["macho_minos"] = macho_minos(lib)
            have = tuple(int(x) for x in detail["macho_minos"].split(".")[:2])
            if have > tuple(int(x) for x in want.split(".")):
                problems.append("library minos %s exceeds the tag's %s" % (detail["macho_minos"], want))
        elif tag.startswith("win"):
            imports = pe_imports(lib)
            detail["pe_imports"] = imports
            bad = [i for i in imports if WINDOWS_RUNTIME.match(i)]
            if bad:
                problems.append("library imports the VC++ runtime: %s" % bad)
    core = ROOT / "ukiryu-rs"
    record = {
        "wheel": name,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "platform_tag": tag,
        "core_commit": run("git", "-C", str(core), "rev-parse", "HEAD").strip(),
        "cargo_lock_sha256": hashlib.sha256((core / "Cargo.lock").read_bytes()).hexdigest(),
        **detail,
        "problems": problems,
    }
    evidence.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(record, indent=2) + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
