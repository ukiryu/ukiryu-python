"""Record which core this binding was built and tested against.

Prints a JSON record: the ukiryu-rs submodule commit, the sha256 of its
Cargo.lock and of the loaded C library, and the core version reported over
the wire, which must equal the workspace version in ukiryu-rs/Cargo.toml.

Usage: python scripts/engine_provenance.py [OUTPUT.json]
(UKIRYU_CDYLIB must name the built library.)
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "ukiryu-rs"
sys.path.insert(0, str(ROOT / "src"))

import ukiryu  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    commit = subprocess.run(["git", "-C", str(CORE), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True).stdout.strip()
    pinned = subprocess.run(["git", "-C", str(ROOT), "ls-tree", "HEAD", "ukiryu-rs"], check=True,
                            capture_output=True, text=True).stdout.split()
    cargo = (CORE / "Cargo.toml").read_text(encoding="utf-8")
    match = re.search(r'^\[workspace\.package\][^\[]*?^version\s*=\s*"([^"]+)"', cargo,
                      re.MULTILINE | re.DOTALL)
    info = ukiryu.library_info()
    core = ukiryu.core_version()
    record = {
        "core_submodule_commit": commit,
        "core_pinned_gitlink": pinned[2] if len(pinned) >= 3 else None,
        "cargo_lock_sha256": sha256(CORE / "Cargo.lock"),
        "library": {"source": info.source, "path": info.path,
                    "sha256": sha256(Path(info.path)), "abi_version": info.abi_version},
        "core_version": core.version,
        "workspace_version": match.group(1) if match else None,
        "binding_version": ukiryu.__version__,
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }
    problems = []
    if record["core_pinned_gitlink"] != commit:
        problems.append("checked-out core %s is not the pinned gitlink %s"
                        % (commit, record["core_pinned_gitlink"]))
    if record["workspace_version"] != core.version:
        problems.append("library reports core %s, workspace is %s"
                        % (core.version, record["workspace_version"]))
    record["problems"] = problems
    text = json.dumps(record, indent=2) + "\n"
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
