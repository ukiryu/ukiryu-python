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

Root ruling R2 rows are judged by the core conformance consumer's rule files,
read unchanged from the core (``ukiryu-rs/ukiryu/tests/conformance``): a
traced migrated expectation (``migrated.json``), the Lutaml declared-field
projection (``projection.json``) or the pinned Yeptris emitter bytes
(``emitter.json``). Every other row compares with its frozen expectation.
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
    "resolve.shell_detect": 17,
    "cli.run": 21,
    "shell.env_var": 176,
    "shell.escape": 3912,
    "shell.format_path": 40,
    "shell.join": 664,
    "shell.needs_quoting": 3912,
    "shell.quote": 3912,
    "lint.string": 70,
    "yaml.load": 81,
    "yaml.model_load": 62,
    "yaml.lutaml_roundtrip": 2,
}

# Every operation is dispatched. This explicit empty map makes a new exclusion
# visible to the coverage pin instead of silently shrinking the replay.
NOT_DISPATCHED = {}

MANIFEST_TOTAL = 14177

# Ops whose rows the R2 rule files judge here, and how many rows they list
# among them. All other rows keep their frozen expectations.
R2_RULE_OPS = ("lint.string", "yaml.load", "yaml.model_load", "yaml.lutaml_roundtrip",
               "resolve.shell_detect")
R2_RULE_ROWS = 35


def corpus_dir() -> Path:
    path = os.environ.get("UKIRYU_CONFORMANCE_DIR")
    if not path:
        if os.environ.get("CI"):
            raise AssertionError("UKIRYU_CONFORMANCE_DIR must be set in CI")
        raise unittest.SkipTest("UKIRYU_CONFORMANCE_DIR is not set")
    return Path(path).resolve()


def rules_dir() -> Path:
    """The core's R2 rule files, beside the corpus in the core checkout."""
    return corpus_dir().parent.parent / "ukiryu" / "tests" / "conformance"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def host() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def applicable(row) -> bool:
    """Only the frozen CLI and shell groups use explicit virtual platforms.

    Any new platform/capability restriction still needs a deliberate runner
    change. Real process execution remains outside the shared corpus.
    """
    platforms = row["applicability"]["platforms"]
    requires = row["applicability"]["requires"]
    if row["op"] == "cli.run":
        return (row["kind"] == "cli-output" and platforms == ["linux"]
                and requires == ["cli", "process", "fs"])
    if row["op"] == "resolve.shell_detect":
        return row["kind"] == "resolve" and platforms == ["linux", "macos"] and requires == []
    return host() in platforms and requires == []


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

    def cli_snapshot(self):
        """Caller-owned virtual inputs, shared with the portable core renderer."""
        if not hasattr(self, "_cli_snapshot"):
            files = {}
            for entry in self.manifest["inputs"]:
                path = self.inputs / entry["path"]
                if sha256(path) != entry["sha256"]:
                    raise AssertionError("CLI input digest differs: %s" % entry["path"])
                files["/snapshot/inputs/" + entry["path"]] = self.read_input(entry["path"])
            self._cli_snapshot = {"files": files, "executables": {}, "probes": [],
                                  "definitions": []}
        return self._cli_snapshot

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
        if op == "resolve.shell_detect":
            # The corpus captures Unix detection. An explicit virtual Linux
            # platform replays the same input on Windows without reading ENV.
            return ukiryu.detect_shell(platform="linux", env=i["env"])
        if op == "cli.run":
            argv = [arg.replace("${INPUTS}", "/snapshot/inputs")
                    .replace("${HOME}", "/snapshot/home") for arg in i["argv"]]
            result = ukiryu.render_cli(argv, platform="linux", env={
                "HOME": "/snapshot/home", "SHELL": "/bin/bash", "PATH": "",
                "TZ": "UTC", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "dumb",
                "COLUMNS": "100", "THOR_COLUMNS": "100",
                "UKIRYU_REGISTER": "/snapshot/inputs/register",
            }, snapshot=self.cli_snapshot())
            return {key: value.replace("/snapshot/inputs", "${INPUTS}")
                    .replace("/snapshot/home", "${HOME}") if isinstance(value, str) else value
                    for key, value in result.items()}
        if op == "resolve.extract_version":
            return ukiryu.extract_version(decode(i["output"]), i["pattern"])
        if op == "resolve.range":
            return ukiryu.version_range_matches(decode(i["scheme"]), i["range"],
                                                decode(i["bounds"]), i["version"])
        if op == "resolve.version_spec":
            index = self.read_input("register/tools/%s/index.yaml" % i["tool"])
            return ukiryu.find_version_spec(index, i["implementation"], i["version"])
        if op == "lint.string":
            return ukiryu.lint_string(self.document(i))
        if op == "yaml.load":
            return ukiryu.safe_load(self.document(i))
        if op == "yaml.model_load":
            return ukiryu.load_model(i["model"], self.input_path(i["file"]))
        if op == "yaml.lutaml_roundtrip":
            # emitted = from_yaml(text).to_yaml; from_yaml(emitted).to_yaml == emitted
            emitted = ukiryu.model_to_yaml(i["model"], self.document(i))
            return {"emitted": emitted,
                    "reparsed_equal": ukiryu.model_to_yaml(i["model"], emitted) == emitted}
        if op.startswith("shell."):
            args = dict(i)
            shell = Shell(args.pop("shell"))
            return getattr(shell, op[len("shell."):])(**args)
        raise AssertionError("no dispatch for %s" % op)

    def outcome(self, row):
        """The row's result tuple and the error's structured detail."""
        try:
            return ("ok", self.scrub(self.run(row["op"], row["input"]))), None
        except UkiryuError as error:
            if error.source_class is None:
                return ("wire_error", error.code, error.message), None
            return ("error", error.source_class, self.scrub(error.message)), error.detail

    @staticmethod
    def expected(row):
        return expected(row["expect"])


def expected(expect):
    if "ok" in expect:
        return ("ok", decode(expect["ok"]))
    return ("error", expect["error"]["class"], expect["error"]["message"])


def canonical(value) -> str:
    """Key-sorted compact JSON, the form ``frozen_expect_sha256`` digests."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def mismatch(want, error_match, want_detail, have, detail):
    """None when the outcome satisfies the expectation, else the reason."""
    if want[0] == "ok":
        return None if same(have, want) else "got %r, want %r" % (have, want)
    if have[0] != "error":
        return "expected error, got %r" % (have,)
    if have[1] != want[1]:
        return "error class differs: got %r, want %r" % (have, want)
    if error_match == "class_and_message" and have[2] != want[2]:
        return "error message differs: got %r, want %r" % (have[2], want[2])
    if want_detail is not None and detail != want_detail:
        return "error detail differs: got %r, want %r" % (detail, want_detail)
    return None


def project(value, classes):
    """The frozen snapshot without framework-private ivars, on objects of a
    projected class whose ivars are exactly declared + framework-private."""
    if isinstance(value, list):
        return [project(x, classes) for x in value]
    if not isinstance(value, dict):
        return value
    spec = classes.get(value.get("class")) if isinstance(value.get("class"), str) else None
    out = {}
    for key, x in value.items():
        x = project(x, classes)
        if spec is not None and key == "ivars" and isinstance(x, dict):
            want = set(spec["declared"]) | set(spec["framework_private"])
            if set(x) != want:
                raise ValueError("%s ivars %r are not exactly declared + framework-private %r"
                                 % (value["class"], sorted(x), sorted(want)))
            x = {n: v for n, v in x.items() if n in spec["declared"]}
        out[key] = x
    return out


class Rules:
    """How each row is judged: its frozen expectation, or the one R2 rule
    that lists it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.migrated_spec = json.loads((root / "migrated.json").read_text(encoding="utf-8"))
        self.projection_spec = json.loads((root / "projection.json").read_text(encoding="utf-8"))
        self.emitter_spec = json.loads((root / "emitter.json").read_text(encoding="utf-8"))
        self.migrated = {m["id"]: m for m in self.migrated_spec["rows"]}
        self.classes = {c["class"]: c for c in self.projection_spec["classes"]}
        self.projected = set(self.projection_spec["rows"])
        self.emitter = {r["id"]: root / r["golden"] for r in self.emitter_spec["rows"]}

    def listed(self):
        """The ids each rule lists (an id under two rules appears twice)."""
        return list(self.migrated) + sorted(self.projected) + list(self.emitter)

    def judge(self, row, have, detail):
        error_match = row.get("error_match", "")
        if row["op"] not in R2_RULE_OPS:
            return mismatch(expected(row["expect"]), error_match, None, have, detail)
        m = self.migrated.get(row["id"])
        if m is not None:
            return mismatch(expected(m["expect"]), error_match, m.get("detail"), have, detail)
        if row["id"] in self.projected:
            try:
                want = expected(project(row["expect"], self.classes))
            except ValueError as error:
                return "projection: %s" % error
            return mismatch(want, error_match, None, have, detail)
        golden = self.emitter.get(row["id"])
        if golden is not None:
            return emitter_mismatch(row["expect"], golden, have)
        return mismatch(expected(row["expect"]), error_match, None, have, detail)


def emitter_mismatch(expect, golden: Path, have):
    """``{emitted, reparsed_equal}``: ``reparsed_equal`` as frozen, ``emitted``
    canonically equal (core ``safe_load``) to the frozen Psych text and
    byte-equal to the pinned Yeptris golden."""
    if "ok" not in expect:
        return "emitter rule needs an ok expectation"
    if have[0] != "ok":
        return "expected ok, got %r" % (have,)
    got, frozen = have[1], decode(expect["ok"])
    if not isinstance(got, dict) or set(got) != set(frozen):
        return "emitter outcome keys differ: %r" % (got,)
    if not same(got["reparsed_equal"], frozen["reparsed_equal"]):
        return "reparsed_equal differs from the frozen value"
    emitted, psych = got["emitted"], frozen["emitted"]
    if not isinstance(emitted, str) or not isinstance(psych, str):
        return "emitted must be a string"
    try:
        a, b = ukiryu.safe_load(emitted), ukiryu.safe_load(psych)
    except UkiryuError as error:
        return "canonical load failed: %s" % error
    if not same(a, b):
        return "emitted YAML is not canonically equal to the frozen Psych text"
    pinned = golden.read_bytes() if golden.is_file() else None
    if pinned is None:
        return "no pinned Yeptris golden; emitted bytes: %s" % json.dumps(emitted)
    if pinned != emitted.encode("utf-8"):
        return "emitted bytes differ from the pinned Yeptris golden; got %s" % json.dumps(emitted)
    return None


class ConformanceCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus(corpus_dir())
        cls.rules = Rules(rules_dir())

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
            self.assertTrue(applicable(row), row["id"])
            dispatched[op] += 1
            have, detail = self.corpus.outcome(row)
            reason = self.rules.judge(row, have, detail)
            if reason is not None:
                failures.append("%s:\n  %s" % (row["id"], reason))
        self.assertEqual(dict(dispatched), DISPATCHED)
        total = sum(dispatched.values())
        self.assertEqual(total, MANIFEST_TOTAL)
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
        self.assertEqual(self.corpus.outcome(altered), (("ok", ["other.txt"]), None))
        self.assertFalse(same(self.corpus.outcome(altered)[0], Corpus.expected(row)))

    def test_r2_rules_are_traced_and_scoped(self):
        # Each rule lists an existing corpus row, under one rule only, and
        # the rules over the R2 ops cover exactly R2_RULE_ROWS rows.
        rows = {row["id"]: row for row in self.corpus.rows if row["op"] in R2_RULE_OPS}
        all_ids = {row["id"] for row in self.corpus.rows}
        problems = []
        applied = [i for i in self.rules.listed() if i in rows]
        for i in self.rules.listed():
            if i not in all_ids:
                problems.append("%s: no such corpus row" % i)
        for i, n in Counter(applied).items():
            if n > 1:
                problems.append("%s: listed by more than one rule" % i)
        self.assertEqual(len(applied), R2_RULE_ROWS, sorted(applied))
        bases = ("D1", "D2", "version", "emitter", "yaml-reader")
        required = {"D1": ("code", "variant", "stage"), "D2": ("code", "variant", "stage"),
                    "yaml-reader": ("category", "stage")}
        for m in self.rules.migrated_spec["rows"]:
            row = rows.get(m["id"])
            if row is None:
                continue
            basis = m.get("basis")
            if basis not in bases:
                problems.append("migrated %s: unknown basis %r" % (m["id"], basis))
            if basis in ("D1", "D2") and row.get("decision") != basis:
                problems.append("migrated %s: basis %s not the row's decision" % (m["id"], basis))
            if "#" not in m.get("trace", ""):
                problems.append("migrated %s: trace must cite the governing issue" % m["id"])
            frozen = hashlib.sha256(canonical(row["expect"]).encode("utf-8")).hexdigest()
            if m.get("frozen_expect_sha256") != frozen:
                problems.append("migrated %s: frozen expectation changed (now sha256 %s)"
                                % (m["id"], frozen))
            expect = m.get("expect")
            if not (isinstance(expect, dict) and len(expect) == 1
                    and ("ok" in expect or "error" in expect)):
                problems.append("migrated %s: expect must be {ok} or {error}" % m["id"])
            elif "error" in expect:
                for key in required.get(basis, ()):
                    if not isinstance((m.get("detail") or {}).get(key), str):
                        problems.append("migrated %s: error must freeze detail.%s"
                                        % (m["id"], key))
            if expect == row["expect"]:
                problems.append("migrated %s: equals the frozen expectation" % m["id"])
        spec = self.rules.projection_spec
        for key in ("evidence", "trace"):
            if not spec.get(key):
                problems.append("projection.json: missing %s" % key)
        for c in spec["classes"]:
            if not (c.get("class") and c.get("declared") and c.get("framework_private")):
                problems.append("projection %s: incomplete class entry" % c.get("class"))
            if set(c.get("declared", ())) & set(c.get("framework_private", ())):
                problems.append("projection %s: a declared field is private" % c["class"])
        for i in sorted(self.rules.projected):
            row = rows.get(i)
            if row is None:
                continue
            if row["op"] != "yaml.model_load":
                problems.append("projection %s: op is not yaml.model_load" % i)
            try:
                if project(row["expect"], self.rules.classes) == row["expect"]:
                    problems.append("projection %s: removes nothing" % i)
            except ValueError as error:
                problems.append("projection %s: %s" % (i, error))
        for r in self.rules.emitter_spec["rows"]:
            row = rows.get(r["id"])
            if row is None or row["op"] != "yaml.lutaml_roundtrip":
                problems.append("emitter %s: not a yaml.lutaml_roundtrip row" % r["id"])
            if "#" not in r.get("trace", ""):
                problems.append("emitter %s: trace must cite the governing issue" % r["id"])
            if sha256(self.rules.root / r["golden"]) != r.get("golden_sha256"):
                problems.append("emitter %s: golden sha256 differs from the pin" % r["id"])
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
