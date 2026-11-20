"""Focused checks for the two portable groups that used to be excluded.

Run this file to verify 38 affected rows, without replaying the full corpus.
The existing complete replay also dispatches these groups in CI.
"""

import copy
import unittest
from unittest.mock import patch

import ukiryu
from test_corpus import Corpus, Rules, applicable, corpus_dir, rules_dir, same


class PortableCorpus(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.corpus = Corpus(corpus_dir())
        cls.rules = Rules(rules_dir())

    def check_group(self, op, count):
        rows = [row for row in self.corpus.rows if row["op"] == op]
        self.assertEqual(len(rows), count)
        for row in rows:
            with self.subTest(row=row["id"]):
                self.assertTrue(applicable(row))
                have, detail = self.corpus.outcome(row)
                self.assertIsNone(self.rules.judge(row, have, detail), repr((have, detail)))

    def test_every_frozen_shell_detection_row(self):
        self.check_group("resolve.shell_detect", 17)

    def test_every_frozen_cli_row(self):
        self.check_group("cli.run", 21)

    def test_cli_help_mutation_changes_output(self):
        original = self.corpus.run("cli.run", {"argv": ["help"]})
        changed = self.corpus.run("cli.run", {"argv": ["help", "exec"]})
        self.assertFalse(same(original, changed))
        self.assertIn("exec", changed["stdout"])

    def test_shell_detection_ignores_parent_environment(self):
        with patch.dict("os.environ", {"SHELL": "/no/such/shell", "UKIRYU_TEST_SHELL": "cmd"}):
            result = ukiryu.detect_shell(platform="linux", env={"SHELL": "/bin/bash"})
        self.assertEqual(result["detect"], ukiryu.Symbol("bash"))
        with self.assertRaises(ukiryu.UkiryuError) as raised:
            ukiryu.detect_shell(platform="linux", env={"SHELL": "/no/such/shell"})
        self.assertEqual(raised.exception.source_class, "Ukiryu::Errors::UnknownShellError")
        self.assertEqual(raised.exception.detail["code"], "UnknownShell")

    def test_side_effecting_cli_commands_are_refused(self):
        for argv in (["exec", "jq", "process"], ["register", "update"],
                     ["config", "set", "shell", "sh"]):
            with self.subTest(argv=argv), self.assertRaises(ukiryu.UkiryuError) as raised:
                self.corpus.run("cli.run", {"argv": argv})
            self.assertEqual(raised.exception.code, "UNSUPPORTED_CAPABILITY")

    def test_virtual_applicability_cannot_widen_silently(self):
        cli = next(row for row in self.corpus.rows if row["op"] == "cli.run")
        changed = copy.deepcopy(cli)
        changed["applicability"]["platforms"] = ["linux", "windows"]
        self.assertFalse(applicable(changed))
        changed = copy.deepcopy(cli)
        changed["applicability"]["requires"].append("network")
        self.assertFalse(applicable(changed))
        changed = copy.deepcopy(cli)
        changed["kind"] = "resolve"
        self.assertFalse(applicable(changed))

    def test_cli_snapshot_checks_input_digests(self):
        corpus = Corpus(self.corpus.root)
        corpus.manifest["inputs"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(AssertionError, "CLI input digest differs"):
            corpus.cli_snapshot()

    def test_cli_schema_resources_are_refused_on_every_host(self):
        profile = "/snapshot/inputs/profiles/inkscape_1.0.yaml"
        schema = "/snapshot/inputs/external-schema/tool.schema.yaml"
        original = copy.deepcopy(self.corpus.cli_snapshot())
        original["files"][schema] = "type: object\n"
        argv = ["validate", "file", profile, "--schema", schema]
        control = ukiryu.render_cli(argv, platform="linux", env={}, snapshot=original)
        self.assertIn("exit", control)
        for reference in (r"file://D:\a\ukiryu-rs\ukiryu-rs\ukiryu/Cargo.toml",
                          "file:///C:/host/Cargo.toml",
                          "file://localhost/C:/host/Cargo.toml",
                          r"C:\host\Cargo.toml", r"\\server\share\Cargo.toml",
                          "file:///host/Cargo.toml", "../host/Cargo.toml",
                          "https://example.invalid/schema.json"):
            snapshot = copy.deepcopy(original)
            snapshot["files"][schema] = "$ref: %s\n" % reference
            with self.subTest(reference=reference), self.assertRaises(ukiryu.UkiryuError) as raised:
                ukiryu.render_cli(argv, platform="linux", env={}, snapshot=snapshot)
            self.assertEqual(raised.exception.code, "UNSUPPORTED_CAPABILITY")
