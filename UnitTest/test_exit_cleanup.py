"""Exit cleanup only touches temporary fixtures; no SDK, devices or user logs."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
from app.exitCleanup import (
    HELPER_FLAG, cleanup_generated_files, run_cleanup_after_exit_cli,
    schedule_exit_cleanup,
)
from app.paths import ApplicationPaths, PATHS
from app.storageManagement import CleanupResult


class ExitCleanupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.paths = ApplicationPaths(self.root, self.root / "app")

    def put(self, name, data=b"generated"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_closed_app_logs_discovery_and_all_supported_bytecode_are_removed(self):
        disposable = [self.put(name) for name in (
            "assets/user/debug/maafw.log", "assets/user/debug/python_fault_12345.log",
            "assets/user/debug/error/shot.png", "assets/user/archive/history.log",
            "assets/user/sessions/old/run/debug/maafw.log",
            "assets/user/sessions/old/run/python_fault_12345.log",
            "assets/user/sessions/discovery/debug/maafw.log",
            "assets/resources/debug/maafw.log", "debug/old.dmp", "logs/history.log",
            "app/__pycache__/widget.cpython-310.pyc", "__pycache__/main.cpython-310.pyc",
            "assets/agent/__pycache__/main.cpython-310.pyc",
            "assets/resources/agent/__pycache__/action.cpython-310.pyc",
        )]
        protected = [self.put(name) for name in (
            "assets/user/config/maa_config.json", "assets/user/config/user_config.json",
            "assets/user/sessions/old/run/config.json", "assets/resources/interface.json",
            "assets/resources/pipeline/main.json", "assets/resources/image/template.png",
            "assets/resources/model/model.onnx", "assets/saved/account.json",
            "app/source.py", "assets/agent/action.py", "assets/resources/agent/action.py",
            ".cache/developer.log", "assets/user/.cache/keep.log",
            "app/.cache/__pycache__/keep.pyc", ".venv/__pycache__/keep.pyc",
        )]
        with patch("app.exitCleanup.process_active", return_value=False), \
                patch("app.storageManagement.process_active", return_value=False):
            result = cleanup_generated_files(self.paths)
        self.assertEqual(result.files, len(disposable))
        self.assertFalse(result.errors)
        self.assertTrue(all(not path.exists() for path in disposable))
        self.assertTrue(all(path.exists() for path in protected))

    def test_another_live_client_defers_shared_cleanup(self):
        marker = self.put("assets/user/debug/python_fault_12345.log")
        log = self.put("assets/user/debug/maafw.log")
        cache = self.put("app/__pycache__/widget.pyc")
        with patch("app.exitCleanup.process_active", return_value=True):
            result = cleanup_generated_files(self.paths)
        self.assertEqual(result.files, 0)
        self.assertEqual(result.skipped, 1)
        self.assertTrue(marker.exists() and log.exists() and cache.exists())

    def test_running_session_remains_protected_after_the_gui_exits(self):
        marker = self.put(f"assets/user/sessions/live/run/python_fault_{os.getpid()}.log")
        live = self.put("assets/user/sessions/live/run/debug/maafw.log")
        old = self.put("assets/user/sessions/old/run/debug/maafw.log")
        result = cleanup_generated_files(self.paths)
        self.assertTrue(marker.exists() and live.exists())
        self.assertFalse(old.exists())
        self.assertEqual(result.files, 1)

    def test_cli_waits_for_the_inherited_handle_before_cleanup(self):
        events = []
        kernel = MagicMock()
        kernel.WaitForSingleObject.side_effect = lambda *_args: events.append("wait") or 0
        kernel.CloseHandle.side_effect = lambda *_args: events.append("close")
        with patch("app.exitCleanup._process_api", return_value=kernel), \
                patch("app.exitCleanup.cleanup_generated_files", side_effect=lambda: events.append("cleanup") or CleanupResult()):
            self.assertEqual(run_cleanup_after_exit_cli([HELPER_FLAG, "123"]), 0)
        self.assertEqual(events, ["wait", "close", "cleanup"])
        kernel.WaitForSingleObject.assert_called_once_with(123, 0xFFFFFFFF)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_invalid_or_failed_wait_never_deletes_files(self):
        kernel = MagicMock()
        kernel.WaitForSingleObject.return_value = 0xFFFFFFFF
        with patch("app.exitCleanup._process_api", return_value=kernel), \
                patch("app.exitCleanup.cleanup_generated_files") as cleanup:
            for arguments in ([], [HELPER_FLAG], [HELPER_FLAG, "0"], [HELPER_FLAG, "-1"],
                              [HELPER_FLAG, "bad"], [HELPER_FLAG, "1", str(self.root)],
                              [HELPER_FLAG, str(1 << 64)]):
                self.assertEqual(run_cleanup_after_exit_cli(arguments), 2)
            self.assertEqual(run_cleanup_after_exit_cli([HELPER_FLAG, "123"]), 1)
        cleanup.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_launch_is_headless_and_uses_the_base_interpreter_or_frozen_entry(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                kernel = MagicMock()
                kernel.OpenProcess.return_value = 123
                with patch("app.exitCleanup._process_api", return_value=kernel), \
                        patch.object(sys, "frozen", frozen, create=True), \
                        patch.object(sys, "executable", "MFWSide.exe"), \
                        patch.object(sys, "_base_executable", "base-python.exe", create=True), \
                        patch("app.exitCleanup.subprocess.Popen") as launch:
                    schedule_exit_cleanup()
                command = launch.call_args.args[0]
                self.assertEqual(command[-2:], [HELPER_FLAG, "123"])
                self.assertEqual(command[0], "MFWSide.exe" if frozen else "base-python.exe")
                if not frozen:
                    self.assertEqual(command[1], "-B")
                    self.assertEqual(Path(command[2]).name, "main.py")
                options = launch.call_args.kwargs
                self.assertEqual(options["startupinfo"].lpAttributeList, {"handle_list": [123]})
                self.assertEqual(options["creationflags"], subprocess.CREATE_NO_WINDOW)
                self.assertEqual(options["cwd"], str(PATHS.root_dir))
                self.assertEqual(options["env"]["PYTHONDONTWRITEBYTECODE"], "1")
                if frozen:
                    self.assertEqual(options["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
                kernel.CloseHandle.assert_called_once_with(123)

    def test_failed_launch_closes_the_parent_handle(self):
        kernel = MagicMock()
        kernel.OpenProcess.return_value = 123
        with patch("app.exitCleanup._process_api", return_value=kernel), \
                patch("app.exitCleanup.subprocess.Popen", side_effect=OSError("cannot launch")), \
                self.assertRaises(OSError):
            schedule_exit_cleanup()
        kernel.CloseHandle.assert_called_once_with(123)

    def test_main_policy_honors_disabled_and_reset_settings(self):
        window = SimpleNamespace(settings_panel=MagicMock(), _reset_settings_pending=False)
        window.settings_panel.auto_cleanup_on_exit_enabled.return_value = False
        with patch("app.exitCleanup.schedule_exit_cleanup") as schedule:
            main._schedule_exit_cleanup(window)
            schedule.assert_not_called()
            window.settings_panel.auto_cleanup_on_exit_enabled.return_value = True
            main._schedule_exit_cleanup(window)
            schedule.assert_called_once_with()
            schedule.reset_mock()
            window.settings_panel.auto_cleanup_on_exit_enabled.return_value = False
            window._reset_settings_pending = True
            main._schedule_exit_cleanup(window)
            schedule.assert_called_once_with()

    def test_real_source_helper_cleans_only_a_copied_temporary_app_after_exit(self):
        source = Path(__file__).resolve().parents[1]
        for relative in ("main.py", "app/__init__.py", "app/paths.py",
                         "app/storageManagement.py", "app/exitCleanup.py"):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, target)
        log = self.put("assets/user/debug/maafw.log")
        cache = self.put("app/__pycache__/unused.pyc")
        config = self.put("assets/user/config/maa_config.json")
        code = (
            "from pathlib import Path; import time; "
            "from app.exitCleanup import schedule_exit_cleanup; "
            "schedule_exit_cleanup(); time.sleep(0.2); "
            "assert Path('assets/user/debug/maafw.log').is_file()"
        )
        result = subprocess.run(
            [getattr(sys, "_base_executable", sys.executable), "-B", "-c", code],
            cwd=self.root, capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        deadline = time.monotonic() + 10
        while (log.exists() or cache.exists()) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(log.exists())
        self.assertFalse(cache.exists())
        self.assertTrue(config.exists())


if __name__ == "__main__":
    unittest.main()
