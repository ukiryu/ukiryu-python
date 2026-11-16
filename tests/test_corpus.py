"""The shared conformance corpus, through the public Python API.

Each row's wire-shaped input is decoded to Python values and passed to the
typed API; the typed result is compared with the row's decoded expectation
(captured from the pinned Ruby oracle). Comparison is strict about types:
``True`` is not ``1`` and ``1.0`` is not ``1``.

``UKIRYU_CONFORMANCE_DIR`` names the corpus (``ukiryu-rs/corpus/conformance``
in the core submodule). Its manifest is checked first, so a changed or
partial corpus fails loudly instead of shrinking the run.

Every corpus op is either dispatched here or declared in ``NOT_DISPATCHED``
with its count and the reason it has no wire operation; together they must
cover the manifest total.
"""

import dataclasses
import hashlib
import json
import os
import sys
import unittest
from collections import Counter
from pathlib import Path

import ukiryu
from ukiryu import Map, Register, Shell, Symbol, UkiryuError
from ukiryu.values import decode

KINDS = ("build", "cli-output", "escape", "lint", "resolve", "schema", "validate", "yaml")

# Rows each dispatched op must run; a corpus change changes these.
DISPATCHED = {
    "build.args": 485,
    "type.validate": 60,
    "schema.validate_profile": 135,
    "schema.definition_validate": 58,
    "resolve.extract_version": 372,
    "resolve.range": 68,
    "resolve.version_spec": 130,
    "shell.env_var": 176,
    "shell.escape": 3912,
    "shell.format_path": 40,
    "shell.join": 664,
    "shell.needs_quoting": 3912,
    "shell.quote": 3912,
}

# Ops with no wire operation in ABI version 1, so no binding can run them.
NOT_DISPATCHED = {
    "resolve.shell_detect": (17, "host shell detection is not on the wire (owner decision D2 open)"),
    "lint.string": (70, "no lint operation on the wire"),
    "yaml.load": (81, "YAML loading is internal to the core; no yaml operation on the wire"),
    "yaml.model_load": (62, "YAML loading is internal to the core; no yaml operation on the wire"),
    "yaml.lutaml_roundtrip": (2, "YAML loading is internal to the core; no yaml operation on the wire"),
    "cli.run": (21, "CLI process rows belong to the CLI binary, not the library API"),
}

MANIFEST_TOTAL = 14177


def corpus_dir() -> Path:
    path = os.environ.get("UKIRYU_CONFORMANCE_DIR")
    if not path:
        if os.environ.get("CI"):
            raise AssertionError("UKIRYU_CONFORMANCE_DIR must be set in CI")
        raise unittest.SkipTest("UKIRYU_CONFORMANCE_DIR is not set")
    return Path(path).resolve()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def host() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def same(a, b) -> bool:
    """Structural equality that keeps bool, int and float apart."""
    if type(a) is not type(b):
        return False
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, Map):
        return same(a.pairs, b.pairs)
    if dataclasses.is_dataclass(a):
        return all(same(getattr(a, f.name), getattr(b, f.name)) for f in dataclasses.fields(a))
    return a == b


class Corpus:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.inputs = root / "inputs"
        self.manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        self.rows = []
        for kind in KINDS:
            text = (root / "cases" / ("%s.jsonl" % kind)).read_text(encoding="utf-8")
            self.rows += [json.loads(line) for line in text.split("\n") if line.strip()]

    def input_path(self, rel: str) -> str:
        return str(self.inputs / rel)

    def read_input(self, rel: str) -> str:
        return (self.inputs / rel).read_bytes().decode("utf-8")

    def document(self, i) -> str:
        return i["yaml"] if i.get("yaml") is not None else self.read_input(i["file"])

    def scrub(self, value):
        base = str(self.inputs)
        if isinstance(value, str):
            return value.replace(base, "${INPUTS}")
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        if isinstance(value, tuple):
            return tuple(self.scrub(v) for v in value)
        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items()}
        if isinstance(value, Map):
            return Map(tuple((self.scrub(k), self.scrub(v)) for k, v in value.pairs))
        return value

    # One public API call per op; the result in the expectation's shape.
    def run(self, op: str, i):
        if op == "build.args":
            tool = Register(self.input_path("register")).version_file(
                i["tool"], implementation=i["implementation"], file=i["file"],
                platform=i["platform"], shell=i["shell"])
            return list(tool.build(i["command"], decode(i["params"])).args)
        if op == "type.validate":
            options = decode(i["options"]) if i.get("options") is not None else None
            return ukiryu.validate_type(decode(i["value"]), decode(i["type"]), options)
        if op == "schema.validate_profile":
            schema = i.get("schema")
            return ukiryu.validate_profile(self.document(i),
                                           self.input_path(schema) if schema else None)
        if op == "schema.definition_validate":
            schema = i.get("schema")
            report = ukiryu.validate_definition(self.document(i),
                                                self.input_path(schema) if schema else None)
            return Map(tuple((Symbol(name), value) for name, value in (
                ("valid", report.valid), ("errors", list(report.errors)),
                ("warnings", list(report.warnings)), ("schema_path", report.schema_path),
                ("error_count", report.error_count), ("warning_count", report.warning_count))))
        if op == "resolve.extract_version":
            return ukiryu.extract_version(decode(i["output"]), i["pattern"])
        if op == "resolve.range":
            return ukiryu.version_range_matches(decode(i["scheme"]), i["range"],
                                                decode(i["bounds"]), i["version"])
        if op == "resolve.version_spec":
            index = self.read_input("register/tools/%s/index.yaml" % i["tool"])
            return ukiryu.find_version_spec(index, i["implementation"], i["version"])
        if op.startswith("shell."):
            args = dict(i)
            shell = Shell(args.pop("shell"))
            return getattr(shell, op[len("shell."):])(**args)
        raise AssertionError("no dispatch for %s" % op)

    def outcome(self, row):
        try:
            return ("ok", self.scrub(self.run(row["op"], row["input"])))
        except UkiryuError as error:
            if error.source_class is None:
                return ("wire_error", error.code, error.message)
            return ("error", error.source_class, self.scrub(error.message))

    @staticmethod
    def expected(row):
        expect = row["expect"]
        if "ok" in expect:
            return ("ok", decode(expect["ok"]))
        return ("error", expect["error"]["class"], expect["error"]["message"])


class ConformanceCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus(corpus_dir())

    def test_manifest_integrity(self):
        manifest = self.corpus.manifest
        ids = sorted(row["id"] for row in self.corpus.rows)
        self.assertEqual(len(ids), MANIFEST_TOTAL)
        self.assertEqual(manifest["cases"]["total"], MANIFEST_TOTAL)
        self.assertEqual(len(set(ids)), len(ids), "duplicate ids")
        self.assertEqual(hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest(),
                         manifest["cases"]["ids_sha256"])
        self.assertEqual(Counter(row["kind"] for row in self.corpus.rows),
                         Counter(manifest["cases"]["by_kind"]))
        for rel, digest in manifest["cases"]["files"].items():
            self.assertEqual(sha256(self.corpus.root / rel), digest, rel)
        for entry in manifest["inputs"]:
            self.assertEqual(sha256(self.corpus.inputs / entry["path"]), entry["sha256"],
                             entry["path"])

    def test_every_op_is_dispatched_or_declared(self):
        ops = Counter(row["op"] for row in self.corpus.rows)
        unknown = set(ops) - set(DISPATCHED) - set(NOT_DISPATCHED)
        self.assertEqual(unknown, set(), "ops with no dispatch or declaration")
        self.assertEqual({op: ops[op] for op in DISPATCHED}, DISPATCHED)
        self.assertEqual({op: ops[op] for op in NOT_DISPATCHED},
                         {op: count for op, (count, _) in NOT_DISPATCHED.items()})
        self.assertEqual(sum(DISPATCHED.values()) + sum(c for c, _ in NOT_DISPATCHED.values()),
                         MANIFEST_TOTAL)

    def test_rows_match_through_the_python_api(self):
        dispatched = Counter()
        failures = []
        for row in self.corpus.rows:
            op = row["op"]
            if op not in DISPATCHED:
                continue
            self.assertIn(host(), row["applicability"]["platforms"], row["id"])
            self.assertEqual(row["applicability"]["requires"], [], row["id"])
            dispatched[op] += 1
            have = self.corpus.outcome(row)
            want = self.corpus.expected(row)
            if not same(have, want):
                failures.append("%s:\n  got  %r\n  want %r" % (row["id"], have, want))
        self.assertEqual(dict(dispatched), DISPATCHED)
        total = sum(dispatched.values())
        self.assertEqual(total, 13924)
        report = os.environ.get("UKIRYU_CORPUS_REPORT")
        if report:
            Path(report).write_text(json.dumps({
                "platform": host(), "dispatched": dict(dispatched), "total": total,
                "failures": len(failures),
                "failed_ids": [f.split(":\n", 1)[0] for f in failures],
            }, indent=2) + "\n", encoding="utf-8")
        self.assertEqual(failures, [], "%d of %d rows differ:\n%s"
                         % (len(failures), total, "\n".join(failures)))

    def test_altered_input_changes_the_result(self):
        # Control: a match is not an echo of the expectation.
        row = next(r for r in self.corpus.rows
                   if r["id"] == "build.bzip2.default.1-0.compress.all-arguments")
        altered = json.loads(json.dumps(row))
        altered["input"]["params"]["inputs"] = ["other.txt"]
        self.assertEqual(self.corpus.outcome(altered), ("ok", ["other.txt"]))
        self.assertFalse(same(self.corpus.outcome(altered), Corpus.expected(row)))


if __name__ == "__main__":
    unittest.main()
