"""ADB app-player boot wait, exact arguments, cancellation and owned-window minimization."""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from app.adbStartup import launch_adb_program, minimize_adb_window
from app.runtime import AppRuntime, PROGRAM_LAUNCH_ENTRY


class Clock:
    def __init__(self):
        self.now = 0

    def sleep(self, seconds):
        self.now += seconds


class _PatchedTest(unittest.TestCase):
    def enterContext(self, context):
        value = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        return value


class AdbStartupTests(_PatchedTest):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.executable = Path(self.directory.name) / "App Player.exe"
        self.executable.touch()
        self.settings = {"manual_path": f'"{self.executable}" -v 0'}
        self.runtime = SimpleNamespace(log_sink=MagicMock(), _user32=MagicMock(), connection={})
        self.process = MagicMock(pid=123)
        self.process.poll.return_value = None
        self.clock = Clock()
        self.factory = self.enterContext(patch("app.adbStartup.subprocess.Popen", return_value=self.process))
        self.enterContext(patch("app.adbStartup.time.monotonic", side_effect=lambda: self.clock.now))
        self.enterContext(patch("app.adbStartup.time.sleep", side_effect=self.clock.sleep))

    def test_exact_target_arguments_shell_disabled_and_full_minute_before_success(self):
        self.assertTrue(launch_adb_program(self.runtime, self.settings)[0])
        self.assertGreaterEqual(self.clock.now, 60)
        self.assertLess(self.clock.now, 60.2)
        self.assertEqual(self.factory.call_args.args[0], [str(self.executable.resolve()), "-v", "0"])
        self.assertIs(self.factory.call_args.kwargs["shell"], False)
        self.runtime._user32.ShowWindow.assert_not_called()
        self.process.terminate.assert_not_called()

    def test_cancel_before_spawn_or_during_wait_never_terminates_user_process(self):
        self.assertFalse(launch_adb_program(self.runtime, self.settings, lambda: True)[0])
        self.factory.assert_not_called()
        result = launch_adb_program(self.runtime, self.settings, lambda: self.clock.now >= 2)
        self.assertFalse(result[0])
        self.assertIn("취소", result[1])
        self.assertLess(self.clock.now, 3)
        self.process.terminate.assert_not_called()

    def test_invalid_manual_target_or_missing_pid_does_not_proceed(self):
        self.assertFalse(launch_adb_program(self.runtime, {})[0])
        self.factory.assert_not_called()
        self.process.pid = None
        self.assertFalse(launch_adb_program(self.runtime, self.settings)[0])
        self.assertEqual(self.clock.now, 0)

    def test_nonzero_process_exit_aborts_before_completion(self):
        self.process.poll.return_value = 2
        result = launch_adb_program(self.runtime, self.settings)
        self.assertFalse(result[0])
        self.assertIn("비정상 종료", result[1])
        self.assertEqual(self.clock.now, 0)

    def test_successful_launcher_handoff_still_waits_full_minute(self):
        self.process.poll.return_value = 0
        self.assertTrue(launch_adb_program(self.runtime, self.settings)[0])
        self.assertGreaterEqual(self.clock.now, 60)

    def test_minimize_only_exact_launched_pid_once_and_never_resize_restore(self):
        with patch("app.adbStartup.Toolkit.find_desktop_windows", return_value=[SimpleNamespace(hwnd=88, window_name="App Player")]), \
                patch("app.monitoring.window_process_info", return_value=(123, str(self.executable.resolve()))):
            self.assertTrue(launch_adb_program(self.runtime, self.settings, minimize=True)[0])
            self.assertTrue(minimize_adb_window(self.runtime))
        self.runtime._user32.ShowWindow.assert_called_once_with(88, 7)
        self.runtime._user32.MoveWindow.assert_not_called()
        self.runtime._user32.SetWindowPlacement.assert_not_called()

    def test_unrelated_window_is_never_minimized_and_missing_window_is_reported(self):
        with patch("app.adbStartup.Toolkit.find_desktop_windows", return_value=[SimpleNamespace(hwnd=88, window_name="App Player")]), \
                patch("app.monitoring.window_process_info", return_value=(999, str(self.executable.resolve()))):
            self.assertTrue(launch_adb_program(self.runtime, self.settings, minimize=True)[0])
        self.runtime._user32.ShowWindow.assert_not_called()
        self.assertTrue(any("최소화 창을 확인하지 못했습니다" in call.args[0]
                            for call in self.runtime.log_sink._emit.call_args_list))

    def test_invalid_explicit_window_aborts_without_touching_another_process(self):
        self.runtime.connection = {"emulator": {"hwnd": 88, "pid": 123}}
        with patch("app.monitoring.window_process_info", return_value=(999, "other.exe")):
            self.assertFalse(launch_adb_program(self.runtime, self.settings, minimize=True)[0])
        self.runtime._user32.ShowWindow.assert_not_called()

    def test_auxiliary_window_without_title_is_not_a_minimize_target(self):
        self.runtime._adb_launcher_pid = 123
        self.runtime._adb_launcher_path = self.executable.resolve()
        with patch("app.adbStartup.Toolkit.find_desktop_windows", return_value=[SimpleNamespace(hwnd=88, window_name="")]), \
                patch("app.monitoring.window_process_info") as query:
            self.assertTrue(minimize_adb_window(self.runtime))
        query.assert_not_called()
        self.runtime._user32.ShowWindow.assert_not_called()


class AdbRuntimeIntegrationTests(_PatchedTest):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.executable = Path(self.directory.name) / "player.exe"
        self.adb = Path(self.directory.name) / "adb.exe"
        self.executable.touch()
        self.adb.touch()
        interface = {"controller": [{"name": "Adb", "type": "Adb"}],
                     "resource": [{"path": ["."]}], "task": []}
        with patch.object(AppRuntime, "_load_interface", return_value=interface), \
                patch("app.runtime.create_user32", return_value=MagicMock()):
            self.runtime = AppRuntime()
        self.runtime.resource = MagicMock(loaded=True)
        self.runtime._resource_loaded = self.runtime._toolkit_initialized = True
        self.settings = {"manual_path": f'"{self.executable}" -v 0',
                         "connection": {"adb_path": str(self.adb), "address": "127.0.0.1:5555"}}
        self.clock = Clock()
        self.enterContext(patch("app.adbStartup.time.monotonic", side_effect=lambda: self.clock.now))
        self.enterContext(patch("app.adbStartup.time.sleep", side_effect=self.clock.sleep))
        process = MagicMock(pid=123)
        process.poll.return_value = None
        self.launch = self.enterContext(patch("app.adbStartup.subprocess.Popen", return_value=process))
        self.controller = MagicMock(connected=True)
        self.controller.post_connection.return_value = fixtures.make_job()
        self.controller.post_inactive.return_value = fixtures.make_job()
        self.controller.add_sink.return_value = 7
        self.controller_times = []
        def create_controller(**kwargs):
            self.controller_times.append(self.clock.now)
            return self.controller
        self.factory = self.enterContext(patch("app.runtime.AdbController", side_effect=create_controller))
        self.win_factory = self.enterContext(patch("app.runtime.Win32Controller"))
        self.tasker = MagicMock(inited=True, running=False, stopping=False)
        self.tasker.post_task.return_value = fixtures.make_job()
        self.tasker_factory = self.enterContext(patch("app.runtime.Tasker", return_value=self.tasker))
        self.addCleanup(self.runtime.release_session)

    def initialize(self, queue=None, **kwargs):
        return self.runtime.initialize({"name": "Adb", "type": "Adb"}, program_settings=self.settings,
                                       execution_queue=queue or [(PROGRAM_LAUNCH_ENTRY, {}), ("Login", {})], **kwargs)

    def test_main_adb_connects_after_wait_without_win32_window_operations(self):
        self.assertTrue(self.initialize()[0])
        self.assertEqual(self.factory.call_args.kwargs, {"adb_path": str(self.adb), "address": "127.0.0.1:5555"})
        self.assertGreaterEqual(self.controller_times[0], 60)
        self.win_factory.assert_not_called()
        self.runtime._user32.MoveWindow.assert_not_called()
        self.runtime._user32.SetWindowPlacement.assert_not_called()
        self.assertTrue(self.runtime.run_task([("Login", {})])[0])

    def test_without_launch_task_connects_immediately(self):
        self.assertTrue(self.initialize(queue=[("Login", {})])[0])
        self.launch.assert_not_called()
        self.assertEqual(self.controller_times, [0])

    def test_cancel_wait_prevents_controller_and_tasker_creation(self):
        result = self.initialize(cancellation_requested=lambda: self.clock.now >= 2)
        self.assertFalse(result[0])
        self.factory.assert_not_called()
        self.tasker_factory.assert_not_called()

    def test_missing_adb_address_fails_before_player_launch(self):
        self.settings["connection"]["address"] = ""
        result = self.initialize()
        self.assertFalse(result[0])
        self.assertIn("주소", result[1])
        self.launch.assert_not_called()
        self.factory.assert_not_called()

    def test_missing_launch_executable_does_not_create_controller(self):
        self.executable.unlink()
        self.assertFalse(self.initialize()[0])
        self.launch.assert_not_called()
        self.factory.assert_not_called()

    def test_failed_adb_connection_never_binds_or_starts_tasker(self):
        self.controller.post_connection.return_value = fixtures.make_job(False)
        self.assertFalse(self.initialize(queue=[("Login", {})])[0])
        self.tasker_factory.assert_not_called()
        self.tasker.post_task.assert_not_called()

    def test_preview_identity_is_device_not_win32_hwnd(self):
        self.assertTrue(self.initialize(queue=[("Login", {})])[0])
        with patch("app.monitoring.window_process_info") as window_info:
            name, target = self.runtime.preview_connection_target()
        self.assertEqual(name, "Adb")
        self.assertEqual(target.kind, "Adb")
        self.assertEqual(target.key, "127.0.0.1:5555")
        window_info.assert_not_called()

    def test_auto_detected_device_metadata_survives_preview_handoff(self):
        self.settings["connection"]["adb_path"] = ""
        device = SimpleNamespace(address="127.0.0.1:5555", adb_path=self.adb, input_methods=2,
                                 screencap_methods=3, config={"config": "kept"})
        with patch("app.adbStartup.Toolkit.find_adb_devices", return_value=[device]):
            self.assertTrue(self.initialize(queue=[("Login", {})])[0])
        _, target = self.runtime.preview_connection_target()
        self.assertEqual(target.device.adb_path, self.adb)
        self.assertEqual(target.device.config, {"config": "kept"})
        self.assertEqual(self.factory.call_args.kwargs["input_methods"], 2)

    def test_multi_session_launch_uses_own_target_not_main_connection(self):
        from app.sessionRuntime import SessionRuntime
        snapshot = {"interface": self.runtime.interface, "resource": self.runtime.resource_config,
                    "interface_path": str(self.runtime.interface_path)}
        connection = {"preset": "Adb", "target": {"kind": "Adb", "key": "device-b"},
                      "preferences": {"adb_path": str(self.adb)}}
        with patch("app.runtime.create_user32", return_value=MagicMock()):
            session = SessionRuntime(snapshot, connection, self.directory.name)
        self.addCleanup(session.release_session)
        session.resource = MagicMock(loaded=True)
        session._resource_loaded = session._toolkit_initialized = True
        with patch("app.sessionRuntime.AdbController", return_value=self.controller) as factory:
            self.assertTrue(session.initialize(program_settings=self.settings,
                                              execution_queue=[(PROGRAM_LAUNCH_ENTRY, {}), ("Login", {})])[0])
        self.assertGreaterEqual(self.clock.now, 60)
        self.assertEqual(factory.call_args.kwargs["address"], "device-b")
        self.factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
