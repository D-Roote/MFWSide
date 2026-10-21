"""Headless helper routing never imports GUI/SDK or performs a real power action."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from app.afterActions import run_after_exit_cli


class FrozenHelperTests(unittest.TestCase):
    def test_cleanup_routes_before_gui_sdk_and_fault_logging(self):
        loaded = set(sys.modules)
        with patch.object(sys, "argv", ["MFWSide.exe", "--cleanup-after-exit", "999"]), \
                patch.object(sys, "dont_write_bytecode", False), \
                patch("app.exitCleanup.run_cleanup_after_exit_cli", return_value=0) as cleanup:
            self.assertEqual(main.main(), 0)
            self.assertTrue(sys.dont_write_bytecode)
        cleanup.assert_called_once_with(["--cleanup-after-exit", "999"])
        imported = set(sys.modules) - loaded
        self.assertNotIn("PySide6.QtWidgets", imported)
        self.assertNotIn("maa.library", imported)
        self.assertNotIn("app.crashDiagnostics", imported)

    def test_main_routes_helper_before_gui_sdk_paths_and_fault_diagnostics(self):
        loaded = set(sys.modules)
        with patch.object(sys, "argv", ["MFWSide.exe", "--after-exit", "999", "lock", "safe.log"]), \
                patch("app.afterActions._run_after_exit") as action:
            self.assertEqual(main.main(), 0)
        action.assert_called_once_with(999, "lock", "safe.log")
        imported = set(sys.modules) - loaded
        self.assertNotIn("PySide6.QtWidgets", imported)
        self.assertNotIn("maa.library", imported)
        self.assertNotIn("app.crashDiagnostics", imported)

    def test_invalid_arguments_never_run_system_actions(self):
        cases = ([], ["bad"], ["--after-exit", "999", "bad", "safe.log"],
                 ["--after-exit", "-1", "lock", "safe.log"],
                 ["--after-exit", "0", "lock", "safe.log"],
                 ["--after-exit", "18446744073709551616", "lock", "safe.log"],
                 ["--after-exit", "not-a-handle", "lock", "safe.log"],
                 ["--after-exit", "999", "lock", ""],
                 ["--after-exit", "999", "lock", "bad\0path"])
        with patch("app.afterActions._run_after_exit") as action:
            for arguments in cases:
                with self.subTest(arguments=arguments):
                    self.assertEqual(run_after_exit_cli(arguments), 2)
        action.assert_not_called()


if __name__ == "__main__":
    unittest.main()
