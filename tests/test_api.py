"""The typed API against the core: values, errors and protocol strictness."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import threading
import unittest
from unittest.mock import patch

import ukiryu
from ukiryu import (BuiltCommand, DefinitionReport, Map, Regexp, Shell, Symbol, Time, Tool,
                    ToolInfo, UkiryuError)
from ukiryu.errors import ProtocolError
from ukiryu.models import SchemaStage

DEFINITION = (
    "name: 'demo'\nversion: '1.0'\nprofiles:\n- name: default\n"
    "  platforms: [linux, macos, windows]\n  shells: [bash, zsh, fish, sh, powershell, cmd]\n"
    "  executable_name: 'ukiryu-python-demo-tool'\n  commands:\n"
    "  - name: convert\n    arguments:\n    - name: input\n      type: file\n"
    "      position: last\n"
)


class Version(unittest.TestCase):
    def test_core_and_package_versions(self):
        version = ukiryu.core_version()
        self.assertEqual(version.abi_version, 1)
        self.assertRegex(version.version, r"^\d+\.\d+\.\d+")
        self.assertEqual(ukiryu.__version__, "0.1.0")
        self.assertEqual(ukiryu.library_info().abi_version, 1)


class SchemaDiscovery(unittest.TestCase):
    def test_remote_stage_preserves_loaded_and_rejects_invalid_types(self):
        for loaded in (True, False):
            stage = SchemaStage.from_wire({"kind": "remote_fallback", "loaded": loaded})
            self.assertIs(stage.loaded, loaded)
        self.assertIsNone(SchemaStage.from_wire({"kind": "env", "path": "schema.yaml"}).loaded)
        for loaded in (None, 0, "true"):
            with self.subTest(loaded=loaded), self.assertRaises(ProtocolError):
                SchemaStage.from_wire({"kind": "remote_fallback", "loaded": loaded})

    def test_remote_schema_fallback_crosses_the_binding(self):
        class SchemaHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'type: object\nrequired: [missing_remote_property]\n')

            def log_message(self, *_args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), SchemaHandler) as server:
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                url = "http://127.0.0.1:%d/schema.yaml" % server.server_port
                with patch.dict(os.environ, {"UKIRYU_SCHEMA_PATH": "",
                                             "UKIRYU_REMOTE_SCHEMA_URL": url}):
                    report = ukiryu.validate_definition(DEFINITION)
                self.assertEqual(report.schema_stage.kind, "remote_fallback")
                self.assertIs(report.schema_stage.loaded, True)
                self.assertTrue(any("missing_remote_property" in error for error in report.errors))
            finally:
                server.shutdown()
                worker.join()


class Values(unittest.TestCase):
    """Typed values round trip exactly through ``Type.validate``."""

    def check(self, value, type_name):
        self.assertEqual(ukiryu.validate_type(value, Symbol(type_name)), value)

    def test_bigint(self):
        big = 123456789012345678901234567890
        result = ukiryu.validate_type(big, Symbol("integer"))
        self.assertIs(type(result), int)
        self.assertEqual(result, big)
        self.assertEqual(ukiryu.validate_type(-(2 ** 70), Symbol("integer")), -(2 ** 70))

    def test_float(self):
        for value in (0.1, 1e20, -2.5e-10):
            result = ukiryu.validate_type(value, Symbol("float"))
            self.assertIs(type(result), float)
            self.assertEqual(result, value)

    def test_time(self):
        for value in (Time(1705486830, 123456789), Time(-1, 999999999),
                      Time(1705486830, 1, 19800, False)):
            self.check(value, "datetime")

    def test_symbol_and_ordered_map(self):
        self.assertEqual(ukiryu.validate_type("fast", Symbol("symbol"),
                                              {Symbol("values"): ["fast", "slow"]}),
                         Symbol("fast"))
        ordered = Map(((Symbol("b"), 1), ("a", [True, None]), (2, Symbol("x"))))
        self.check(ordered, "hash")

    def test_regexp_option(self):
        with self.assertRaises(UkiryuError) as raised:
            ukiryu.validate_type("ABC", Symbol("string"),
                                 {Symbol("pattern"): Regexp(r"\A[a-z]+\z")})
        self.assertEqual(raised.exception.source_class, "Ukiryu::Errors::ValidationError")
        self.assertEqual(raised.exception.message,
                         r"String does not match required pattern: (?-mix:\A[a-z]+\z)")


class Errors(unittest.TestCase):
    def test_source_errors_keep_class_and_message(self):
        with self.assertRaises(UkiryuError) as raised:
            ukiryu.validate_type(0, Symbol("integer"), {Symbol("range"): [1, 10]})
        error = raised.exception
        self.assertEqual(error.code, "VALIDATION_ERROR")
        self.assertEqual(error.source_class, "Ukiryu::Errors::ValidationError")
        self.assertEqual(error.message, "Integer 0 out of range [1, 10]")
        self.assertIsNone(error.result)

    def test_unknown_shell_is_rejected_not_guessed(self):
        with self.assertRaises(UkiryuError) as raised:
            Shell("no-such-shell").escape("x")
        self.assertIsNotNone(raised.exception.code)

    def test_non_mapping_options_are_a_type_error(self):
        with self.assertRaises(TypeError):
            ukiryu.validate_type("x", Symbol("string"), ["not", "a", "map"])


class ToolApi(unittest.TestCase):
    def setUp(self):
        self.tool = Tool.from_definition(DEFINITION, platform="linux", shell="bash")

    def test_resolve(self):
        info = self.tool.resolve()
        self.assertIsInstance(info, ToolInfo)
        self.assertEqual(info.name, "demo")
        self.assertEqual(info.platform, "linux")
        self.assertEqual(info.shell, "bash")
        self.assertIs(info.available, False)
        self.assertIn("convert", info.commands)

    def test_build(self):
        built = self.tool.build("convert", {"input": "in put.png"})
        self.assertIsInstance(built, BuiltCommand)
        self.assertIsNone(built.executable)
        self.assertEqual(built.command_name, "convert")
        self.assertEqual(built.args, ("in put.png",))
        self.assertIsInstance(built.args, tuple)

    def test_platform_and_shell_are_inputs(self):
        windows = Tool.from_definition(DEFINITION, platform="windows", shell="cmd")
        self.assertEqual(windows.resolve().platform, "windows")


class ShellApi(unittest.TestCase):
    def test_shells_are_independent_of_the_host(self):
        self.assertEqual(Shell("bash").env_var("HOME"), "$HOME")
        self.assertEqual(Shell("cmd").env_var("HOME"), "%HOME%")
        self.assertIs(type(Shell("bash").needs_quoting("a b")), bool)
        self.assertEqual(Shell("bash").quote("a b"), "'a b'")


class Validation(unittest.TestCase):
    def test_definition_report(self):
        report = ukiryu.validate_definition("name: x\n")
        self.assertIsInstance(report, DefinitionReport)
        self.assertIsInstance(report.valid, bool)
        self.assertEqual(report.error_count, len(report.errors))
        self.assertEqual(report.warning_count, len(report.warnings))


if __name__ == "__main__":
    unittest.main()
