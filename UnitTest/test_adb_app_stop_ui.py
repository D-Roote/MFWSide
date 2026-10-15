"""Real Qt completion-worker lifecycle with fake app-stop backend and runtime."""
from dataclasses import asdict
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
import test_after_actions as completion_fixtures
from app.adbAppStop import AdbAppStopTarget
from PySide6.QtCore import QTimer


class GenericAdbCompletionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = completion_fixtures.CompletionUITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.fixture.enable_adb()
        preset = self.window.runtime.interface["controller"][-1]
        self.window.settings_panel.controllers.append(preset)
        self.window.settings_panel.controller_combo.addItem("Android", preset)
        self.window.monitor.select_preset("Android")
        self.window.after_actions.update("close_app", True)
        self.target = AdbAppStopTarget("fake-adb.exe", "usb-device-123",
                                       "11111111-1111-4111-8111-111111111111", "10")
        self.entered, self.release = threading.Event(), threading.Event()
        self.addCleanup(self.cleanup_completion)

    def cleanup_completion(self):
        self.release.set()
        worker = self.window._adb_app_stop_worker
        if worker is not None:
            self.window._closing = True
            worker.requestInterruption()
            self.assertTrue(worker.wait(2000))
            fixtures.QApplication.processEvents()

    def wait_until(self, condition):
        deadline = time.monotonic() + 3
        while not condition() and time.monotonic() < deadline:
            fixtures.QTest.qWait(5)
        self.assertTrue(condition(), "Qt completion callback did not finish")

    def finish_main_task(self, succeeded=True):
        worker = self.fixture.start(succeeded)
        worker.completion_target = self.target
        self.window.on_task_finished()
        return worker

    def test_successful_main_run_stops_actual_device_once_after_cleanup_callbacks(self):
        with patch("app.afterActionUI.AdbAppStopBackend") as backend:
            backend.return_value.stop.return_value = "completed"
            worker = self.fixture.start()
            worker.completion_target = self.target
            self.window.stop_worker = MagicMock(succeeded=True, result_message="released")
            self.window.on_task_finished()
            fixtures.QApplication.processEvents()
            backend.assert_not_called()
            self.window.on_stop_worker_finished()
            self.wait_until(lambda: not self.window._completion_pending)
            backend.return_value.stop.assert_called_once_with(self.target)
            self.window._on_adb_app_stop_finished()
            backend.return_value.stop.assert_called_once()
        self.fixture.backend.close_program.assert_not_called()
        self.assertIsNone(self.window._adb_app_stop_worker)

    def test_failure_stop_and_failed_cleanup_never_create_app_stop_worker(self):
        with patch("app.winUI.AdbAppStopWorker") as app_stop:
            self.finish_main_task(succeeded=False)
            fixtures.QApplication.processEvents()
            app_stop.assert_not_called()
            self.window._run_stop_requested = True
            self.window._run_succeeded = True
            self.window._run_after_actions = {"close_app": True}
            self.window._finish_after_actions()
            fixtures.QApplication.processEvents()
            app_stop.assert_not_called()

    def test_background_stop_keeps_ui_responsive_and_delays_global_action(self):
        def blocked_stop(_target):
            self.entered.set()
            if not self.release.wait(2):
                raise RuntimeError("test timeout")
            return "completed"
        self.window.after_actions.update("system_action", "lock")
        with patch("app.afterActionUI.AdbAppStopBackend") as backend:
            backend.return_value.stop.side_effect = blocked_stop
            self.finish_main_task()
            self.wait_until(self.entered.is_set)
            self.assertFalse(self.window.ui.workStartBtn.isEnabled())
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            self.wait_until(lambda: bool(heartbeat))
            self.fixture.backend.system_action.assert_not_called()
            self.window.on_task_start()
            self.assertIsNone(self.window.worker)
            self.release.set()
            self.wait_until(lambda: not self.window._completion_pending)
        self.fixture.backend.system_action.assert_called_once_with("lock")

    def test_window_close_cancels_and_waits_for_worker_without_power_action(self):
        def blocked_stop(_target):
            self.entered.set()
            self.release.wait(2)
            return "cancelled"
        self.window.after_actions.update("system_action", "lock")
        with patch("app.afterActionUI.AdbAppStopBackend") as backend:
            backend.return_value.stop.side_effect = blocked_stop
            self.window.show()
            self.finish_main_task()
            self.wait_until(self.entered.is_set)
            worker = self.window._adb_app_stop_worker
            self.window.close()
            self.assertTrue(self.window._close_pending)
            self.assertTrue(worker.isInterruptionRequested())
            self.release.set()
            self.wait_until(lambda: self.window._adb_app_stop_worker is None)
            self.wait_until(lambda: not self.window._close_pending)
        self.fixture.backend.system_action.assert_not_called()
        self.fixture.backend.defer_system_action.assert_not_called()

    def test_command_failure_logs_once_and_does_not_hang_completion(self):
        with patch("app.afterActionUI.AdbAppStopBackend") as backend:
            backend.return_value.stop.side_effect = RuntimeError("device changed")
            self.finish_main_task()
            self.wait_until(lambda: not self.window._completion_pending)
        self.assertEqual(self.window.ui.logPrintText.toPlainText().count("device changed"), 1)
        self.assertIsNone(self.window._adb_app_stop_worker)

    def test_multi_instance_group_stops_each_own_device_before_one_global_action(self):
        other = AdbAppStopTarget("fake-adb.exe", "192.0.2.10:5555", self.target.boot_id, "0")
        results = [{"succeeded": True, "connection": {"target": {"kind": "Adb", "key": target.serial}},
                    "adb_app_stop_target": asdict(target)} for target in (self.target, other)]
        actions = {"close_app": True, "close_program": False, "close_emulator": False,
                   "close_maa": False, "system_action": "lock"}
        with patch("app.afterActionUI.AdbAppStopBackend") as backend:
            backend.return_value.stop.return_value = "completed"
            self.window._multi_wave_finished(results, actions)
            self.wait_until(lambda: not self.window._completion_pending)
            self.assertEqual([call.args[0] for call in backend.return_value.stop.call_args_list],
                             [self.target, other])
        self.fixture.backend.system_action.assert_called_once_with("lock")

    def test_multi_group_failure_and_mismatched_target_never_send_commands(self):
        actions = {"close_app": True}
        bad = {"succeeded": False, "connection": {"target": {"kind": "Adb", "key": "wrong"}},
               "adb_app_stop_target": asdict(self.target)}
        with patch("app.winUI.AdbAppStopWorker") as worker:
            self.window._multi_wave_finished([bad], actions)
            worker.assert_not_called()
            bad["succeeded"] = True
            self.window._multi_wave_finished([bad], actions)
            worker.assert_not_called()
        self.assertIn("실행 기기와 종료 대상이 다릅니다", self.window.ui.logPrintText.toPlainText())

    def test_multi_group_shutdown_waits_for_adb_apps_and_avoids_process_exit_helper(self):
        other = AdbAppStopTarget("fake-adb.exe", "192.0.2.10:5555", self.target.boot_id, "0")
        results = [{"succeeded": True, "connection": {"target": {"kind": "Adb", "key": target.serial}},
                    "adb_app_stop_target": asdict(target)} for target in (self.target, other)]
        actions = {"close_app": True, "close_program": False, "close_emulator": False,
                   "close_maa": True, "system_action": "shutdown"}
        stopped = []
        shutdown_states = []

        def stop_target(target):
            self.fixture.backend.system_action.assert_not_called()
            self.fixture.backend.defer_system_action.assert_not_called()
            stopped.append(target)
            return "completed"

        def request_shutdown(action):
            shutdown_states.append((action, list(stopped), self.window._adb_app_stop_worker,
                                    self.window.monitor.ready_to_close, self.window.multi_panel.ready_to_close))

        self.fixture.backend.system_action.side_effect = request_shutdown
        with patch("app.afterActionUI.AdbAppStopBackend") as backend:
            backend.return_value.stop.side_effect = stop_target
            self.window._multi_wave_finished(results, actions)
            self.wait_until(lambda: self.window._closing and not self.window._completion_pending)
            self.window._multi_wave_finished(results, actions)
        self.fixture.backend.system_action.assert_called_once_with("shutdown")
        self.fixture.backend.defer_system_action.assert_not_called()
        self.assertEqual(self.window._deferred_system_action, "")
        self.assertEqual(stopped, [self.target, other])
        self.assertEqual(shutdown_states, [("shutdown", [self.target, other], None, True, True)])


if __name__ == "__main__":
    unittest.main()
