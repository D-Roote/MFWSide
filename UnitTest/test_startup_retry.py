"""Connection retry policy and normal-close restrictions; no real player is touched."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import MagicMock, patch
import unittest

import test_adb_startup as fixtures
import test_runtime_lifecycle as runtime_fixtures
from app.adbStartup import close_adb_player_for_retry, launch_adb_program
from app.afterActions import WindowTarget


class StartupRetryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AdbRuntimeIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.runtime = self.fixture.runtime
        self.settings = self.fixture.settings
        self.settings["restart_on_connection_failure"] = True

    def test_retry_once_only_after_native_cleanup(self):
        self.fixture.controller.post_connection.side_effect = [runtime_fixtures.make_job(False), runtime_fixtures.make_job(True)]
        def closed(*_args):
            self.assertIsNone(self.runtime.controller)
            self.assertIsNone(self.runtime.tasker)
            return True, "closed"
        with patch("app.adbStartup.close_adb_player_for_retry", side_effect=closed) as restart:
            self.assertTrue(self.fixture.initialize()[0])
        restart.assert_called_once()
        self.assertEqual(self.fixture.factory.call_count, 2)

    def test_second_connection_failure_is_not_retried_again(self):
        self.fixture.controller.post_connection.return_value = runtime_fixtures.make_job(False)
        with patch("app.adbStartup.close_adb_player_for_retry", return_value=(True, "closed")) as restart:
            self.assertFalse(self.fixture.initialize()[0])
        restart.assert_called_once()
        self.assertEqual(self.fixture.factory.call_count, 2)

    def test_default_disabled_and_ambiguous_restart_skip(self):
        self.fixture.controller.post_connection.return_value = runtime_fixtures.make_job(False)
        self.settings["restart_on_connection_failure"] = False
        with patch("app.adbStartup.close_adb_player_for_retry") as restart:
            self.assertFalse(self.fixture.initialize()[0])
        restart.assert_not_called()
        self.settings["restart_on_connection_failure"] = True
        with patch("app.adbStartup.close_adb_player_for_retry", return_value=(False, "ambiguous")):
            result = self.fixture.initialize()
        self.assertIn("ambiguous", result[1])

    def test_configured_adb_wait_and_extra_arguments(self):
        self.settings["adb_startup_wait_seconds"] = 15
        self.settings["extra_arguments"] = '--profile "User One"'
        self.assertTrue(self.fixture.initialize()[0])
        self.assertGreaterEqual(self.fixture.controller_times[0], 15)
        self.assertLess(self.fixture.controller_times[0], 16)
        self.assertEqual(self.fixture.launch.call_args.args[0][-2:], ["--profile", "User One"])


class NormalCloseRestrictionsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.executable = Path(directory.name) / "player.exe"
        self.executable.touch()
        self.settings = {"manual_path": f'"{self.executable}" -v 0'}
        self.window = SimpleNamespace(hwnd=55, window_name="Player")
        self.target = WindowTarget(55, 123, str(self.executable))
        self.clock = fixtures.Clock()
        self.context = {"launcher_pid": 123, "launcher_path": self.executable}
        patches = {
            "windows": patch("app.adbStartup.Toolkit.find_desktop_windows", return_value=[self.window]),
            "process": patch("app.monitoring.window_process_info", return_value=(123, str(self.executable))),
            "capture": patch("app.afterActions.capture_window_target", return_value=self.target),
            "close": patch("app.afterActions.WindowsAfterActionBackend.close_program"),
            "active": patch("app.storageManagement.process_active", side_effect=[True, False]),
            "now": patch("app.adbStartup.time.monotonic", side_effect=lambda: self.clock.now),
            "sleep": patch("app.adbStartup.time.sleep", side_effect=self.clock.sleep),
        }
        for name, patcher in patches.items():
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)

    def test_one_exact_process_is_normally_closed_before_success(self):
        self.assertTrue(close_adb_player_for_retry(self.settings, self.context)[0])
        self.close.assert_called_once_with(self.target)

    def test_multiple_or_unrelated_windows_are_not_closed(self):
        self.windows.return_value = [self.window, self.window]
        self.assertFalse(close_adb_player_for_retry(self.settings, self.context)[0])
        self.close.assert_not_called()
        self.windows.return_value = [self.window]
        self.process.return_value = (123, "C:/unrelated.exe")
        self.assertFalse(close_adb_player_for_retry(self.settings, self.context)[0])
        self.close.assert_not_called()

    def test_changed_pid_and_shortcut_without_real_process_are_not_closed(self):
        self.assertFalse(close_adb_player_for_retry(self.settings, {**self.context, "launcher_pid": 999})[0])
        shortcut = self.executable.with_suffix(".lnk")
        shortcut.touch()
        self.assertFalse(close_adb_player_for_retry({"manual_path": f'"{shortcut}"'}, {})[0])
        self.close.assert_not_called()

    def test_preexisting_player_without_ownership_is_never_closed(self):
        result = close_adb_player_for_retry(self.settings, {})
        self.assertFalse(result[0])
        self.assertIn("이 세션에서 실행한", result[1])
        self.close.assert_not_called()

    def test_ignored_close_request_is_bounded_and_never_force_kills(self):
        self.active.side_effect = None
        self.active.return_value = True
        result = close_adb_player_for_retry(self.settings, self.context)
        self.assertFalse(result[0])
        self.assertIn("강제 종료하지 않습니다", result[1])
        self.assertLess(self.clock.now, 11)
        self.close.assert_called_once()

    def test_cancel_before_close_never_touches_window(self):
        self.assertFalse(close_adb_player_for_retry(self.settings, self.context, lambda: True)[0])
        self.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
