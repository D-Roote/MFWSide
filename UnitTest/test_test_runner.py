"""Test the isolated runner without launching Python/Qt or changing error mode."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import run_tests


class IsolatedRunnerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.suite = self.root / "UnitTest"
        self.suite.mkdir()
        for name in ("test_a.py", "test_b.py"):
            (self.suite / name).touch()
        root_patch = patch.object(run_tests, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        child_patch = patch.object(run_tests.subprocess, "run", return_value=SimpleNamespace(returncode=0))
        self.child = child_patch.start()
        self.addCleanup(child_patch.stop)
        self.output = StringIO()

    def run_modules(self, pattern="test_*.py", args=None, collect=False):
        with redirect_stdout(self.output):
            return run_tests.run_isolated_modules(pattern, args or [], collect)

    def test_each_module_gets_a_separate_child_and_unittest_filters(self):
        self.assertEqual(self.run_modules(args=["-q", "-k", "preview"]), 0)
        self.assertEqual(self.child.call_count, 2)
        for invocation, name in zip(self.child.call_args_list, ("test_a.py", "test_b.py")):
            command = invocation.args[0]
            self.assertEqual(command[:2], [run_tests.sys.executable, "-u"])
            self.assertEqual(command[3:], ["--in-process", "-p", name, "-q", "-k", "preview"])
            self.assertFalse(invocation.kwargs["shell"])
            self.assertEqual(invocation.kwargs["cwd"], self.root)
            self.assertEqual(invocation.kwargs["timeout"], 180)

    def test_crash_and_test_failure_remain_failures_without_silent_retry(self):
        for code in (1, -1073741819):
            with self.subTest(code=code):
                self.child.reset_mock()
                self.child.side_effect = [SimpleNamespace(returncode=code), SimpleNamespace(returncode=0)]
                self.assertEqual(self.run_modules(), 1)
                self.assertEqual(self.child.call_count, 2)
        self.assertIn("0xC0000005", self.output.getvalue())

    def test_timeout_is_reported_and_other_module_is_still_checked(self):
        self.child.side_effect = [subprocess.TimeoutExpired("mock-python", 180), SimpleNamespace(returncode=0)]
        self.assertEqual(self.run_modules(), 1)
        self.assertEqual(self.child.call_count, 2)
        self.assertIn("Timed out: test_a.py", self.output.getvalue())

    def test_collection_stress_option_is_forwarded_only_when_requested(self):
        self.assertEqual(self.run_modules(pattern="test_a.py", collect=True), 0)
        self.assertEqual(self.child.call_args.args[0][-1], "--collect-between-tests")

    def test_missing_pattern_and_paths_outside_suite_never_launch_children(self):
        self.assertEqual(self.run_modules("missing_*.py"), 1)
        self.assertEqual(self.run_modules("../UnitTest/test_a.py"), 1)
        self.child.assert_not_called()


if __name__ == "__main__":
    unittest.main()
