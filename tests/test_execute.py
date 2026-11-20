"""``Tool.execute`` runs real processes through the core (Unix hosts, bash).

``jq``, ``printf``, ``cat`` and ``false`` are on every GitHub-hosted Unix
runner; on CI a missing tool fails the test, elsewhere it is skipped and
reported as unavailable rather than passed.
"""

import os
import sys
import unittest

import ukiryu
from ukiryu import ExecutionResult, Time, Tool, UkiryuError

UNIX = not sys.platform.startswith("win")
PLATFORM = "macos" if sys.platform == "darwin" else "linux"
JQ = ("  - name: process\n    arguments:\n    - name: filter\n      type: string\n"
      "      position: first\n")
PRINTF = ("  - name: emit\n    arguments:\n    - name: format\n      type: string\n"
          "      position: first\n")


def definition(name: str, commands: str) -> str:
    return ("name: '%s'\nversion: '1.0'\nprofiles:\n- name: default\n"
            "  platforms: [linux, macos]\n  shells: [bash]\n  executable_name: '%s'\n"
            "  commands:\n%s" % (name, name, commands))


def tool(name: str, commands: str) -> Tool:
    return Tool.from_definition(definition(name, commands), platform=PLATFORM, shell="bash")


@unittest.skipUnless(UNIX, "the execute rows use bash on Unix hosts")
class Execute(unittest.TestCase):
    def available(self, name: str, commands: str) -> Tool:
        found = tool(name, commands)
        info = found.resolve()
        if not info.available:
            if os.environ.get("CI"):
                self.fail("expected on CI: %s" % info.unavailability_reason)
            self.skipTest("UNAVAILABLE (not a pass): %s" % info.unavailability_reason)
        self.assertTrue(info.executable)
        return found

    def test_jq_reads_stdin_and_reports_an_exact_result(self):
        jq = self.available("jq", JQ)
        result = jq.execute("process", {"filter": ".foo", "stdin": '{"foo": "bar"}'},
                            env={}, timeout=60)
        self.assertIsInstance(result, ExecutionResult)
        self.assertEqual(result.stdout, '"bar"\n')
        self.assertEqual(result.stderr, "")
        self.assertIs(result.status, 0)
        self.assertIs(result.success, True)
        self.assertEqual(result.command.arguments, (".foo",))
        self.assertEqual(result.command.tool_name, "jq")
        self.assertEqual(result.command.command_name, "process")
        self.assertIs(type(result.timeout), int)
        self.assertEqual(result.timeout, 60)
        self.assertIs(result.timed_out, False)
        self.assertIsInstance(result.started_at, Time)
        self.assertIsInstance(result.finished_at, Time)
        start = (result.started_at.seconds, result.started_at.nanos)
        finish = (result.finished_at.seconds, result.finished_at.nanos)
        self.assertGreaterEqual(finish, start)
        self.assertIs(type(result.duration), float)

    def test_a_float_timeout_crosses_exactly(self):
        jq = self.available("jq", JQ)
        result = jq.execute("process", {"filter": "."}, env={}, timeout=30.5, stdin="1")
        self.assertEqual(result.stdout, "1\n")
        self.assertIs(type(result.timeout), float)
        self.assertEqual(result.timeout, 30.5)

    def test_zero_timeout_runs_without_a_deadline(self):
        # Ruby ab6df353 and the core's executor_data/observations.json:
        # an explicit integer or Float zero disables the deadline.
        cat = self.available("cat", "  - name: copy\n")
        for timeout in (0, 0.0):
            with self.subTest(timeout=timeout):
                result = cat.execute("copy", env={}, timeout=timeout, stdin="no deadline\n")
                self.assertEqual(result.stdout, "no deadline\n")
                self.assertEqual(result.stderr, "")
                self.assertEqual(result.status, 0)
                self.assertEqual(result.timeout, timeout)
                self.assertIs(type(result.timeout), type(timeout))
                # Ruby metadata compares duration against zero even though
                # the executor treats zero as no deadline.
                self.assertIs(result.timed_out, True)
                self.assertIs(result.success, True)

    def test_stream_bytes_that_are_not_utf8_cross_unchanged(self):
        printf = self.available("printf", PRINTF)
        result = printf.execute("emit", {"format": "\\377A\\000"}, env={}, timeout=60)
        self.assertEqual(result.stdout, b"\xffA\x00")
        cat = self.available("cat", "  - name: copy\n")
        result = cat.execute("copy", env={}, timeout=60, stdin=b"\xff\x00\xfe\n")
        self.assertEqual(result.stdout, b"\xff\x00\xfe\n")
        with self.assertRaises(UkiryuError) as raised:
            cat.execute("copy", {"stdin": "x"}, env={}, timeout=60, stdin="y")
        self.assertEqual(raised.exception.code, "INVALID_ARGUMENT")

    def test_a_failed_command_raises_with_its_result(self):
        false = self.available("false", "  - name: fail\n")
        with self.assertRaises(UkiryuError) as raised:
            false.execute("fail", env={}, timeout=60)
        error = raised.exception
        self.assertEqual(error.code, "EXECUTION_FAILED")
        self.assertEqual(error.source_class, "Ukiryu::Errors::ExecutionError")
        self.assertIsInstance(error.result, ExecutionResult)
        self.assertEqual(error.result.status, 1)
        self.assertIs(error.result.success, False)

    def test_a_missing_executable_behaves_as_the_original(self):
        name = "ukiryu-python-missing-tool"
        with self.assertRaises(UkiryuError) as raised:
            tool(name, "  - name: run\n    subcommand: run\n").execute("run", env={}, timeout=60)
        self.assertEqual(raised.exception.source_class, "Ukiryu::Errors::ExecutionError")
        self.assertEqual(raised.exception.result.status, 127)
        self.assertEqual(raised.exception.result.command.executable, "")

        legacy = tool(name, "  - name: run\n")
        with self.assertRaises(UkiryuError) as raised:
            legacy.execute("run", env={}, timeout=60)
        self.assertEqual((raised.exception.code, raised.exception.source_class,
                          raised.exception.message),
                         ("RUBY_EXCEPTION", "TypeError",
                          "no implicit conversion of nil into String"))
        built = legacy.build("run")
        self.assertIsNone(built.executable)
        self.assertEqual(built.args, ())


class ExecuteArguments(unittest.TestCase):
    def test_env_and_timeout_are_required(self):
        cat = tool("cat", "  - name: copy\n")
        with self.assertRaises(TypeError):
            cat.execute("copy", timeout=5)  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            cat.execute("copy", env={})  # type: ignore[call-arg]


if __name__ == "__main__":
    unittest.main()
