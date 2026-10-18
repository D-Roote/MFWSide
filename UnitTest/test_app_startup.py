"""Automatic startup uses mocked launches/tasks and bounded Qt event processing."""
import threading
from unittest.mock import patch
import unittest

import test_runtime_lifecycle as fixtures
from app.runtime import PROGRAM_LAUNCH_ENTRY


class AppStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.panel = self.window.settings_panel
        self.startup = self.window.app_startup
        self.launch_patch = patch("app.appStartup.request_program_launch")
        self.launch = self.launch_patch.start()
        self.addCleanup(self.launch_patch.stop)
        self.addCleanup(self.stop)

    def stop(self):
        self.startup.shutdown()
        if self.startup.worker:
            self.startup.worker.wait(5000)
        fixtures.QApplication.processEvents()

    def wait_launch(self):
        for _ in range(200):
            if self.startup.worker is None:
                return
            fixtures.QTest.qWait(10)
        self.fail("startup worker did not finish")

    def enable(self, *names):
        for name in names:
            self.panel.startup_checkboxes[name].setChecked(True)

    def target(self):
        path = self.window.runtime.user_dir / "player.exe"
        path.touch()
        self.panel.program_path_input.setText(f'"{path}" -v 0')
        self.panel._apply_program_path()

    def test_defaults_do_not_launch_or_start_tasks(self):
        with patch.object(self.window, "on_task_start") as start:
            self.startup.start()
        start.assert_not_called()
        self.launch.assert_not_called()
        self.assertFalse(self.startup.active)

    def test_minimize_app_is_independent_from_target_minimize(self):
        self.enable("minimize_app")
        with patch.object(self.window, "showMinimized") as minimize:
            self.startup.start()
        minimize.assert_called_once()
        self.launch.assert_not_called()
        self.assertFalse(self.panel.minimize_enabled())

    def test_auto_task_start_is_one_shot_and_respects_queue(self):
        self.enable("auto_start_tasks")
        with patch.object(self.window, "on_task_start") as start:
            self.startup.start()
            self.startup.start()
            self.startup._complete()
        start.assert_called_once()
        self.assertTrue(self.window._startup_queue_override)
        self.launch.assert_not_called()

    def expire_wait(self):
        self.startup.timer.stop()
        self.startup.timer.timeout.emit()

    def test_auto_tasks_wait_for_saved_delay_without_launching_program(self):
        self.enable("auto_start_tasks")
        self.panel.auto_start_wait_spin.setValue(7)
        with patch.object(self.window, "on_task_start") as start:
            self.startup.start()
            self.assertTrue(self.startup.active)
            self.assertTrue(self.startup.timer.isActive())
            self.assertEqual(self.startup.timer.interval(), 7000)
            self.assertIn("7초", self.window.ui.logPrintText.toPlainText())
            start.assert_not_called()
            self.panel.auto_start_wait_spin.setValue(12)
            self.assertEqual(self.startup.timer.interval(), 7000)
            self.expire_wait()
            self.startup.start()
            self.expire_wait()
        start.assert_called_once()
        self.assertFalse(self.startup.active)
        self.assertFalse(self.startup.timer.isActive())
        self.launch.assert_not_called()

    def test_task_delay_is_applied_after_program_loading_delay(self):
        self.target()
        self.enable("launch_program", "auto_start_tasks")
        self.panel.auto_start_wait_spin.setValue(15)
        with patch.object(self.window, "on_task_start") as start:
            self.startup.start()
            self.wait_launch()
            self.assertEqual(self.startup._wait_phase, "program")
            self.expire_wait()
            self.assertEqual(self.startup._wait_phase, "tasks")
            self.assertTrue(self.startup.timer.isActive())
            self.assertEqual(self.startup.timer.interval(), 15000)
            start.assert_not_called()
            self.expire_wait()
            self.expire_wait()
        self.launch.assert_called_once()
        start.assert_called_once()

    def test_cancel_task_delay_prevents_late_auto_start(self):
        self.enable("auto_start_tasks")
        self.panel.auto_start_wait_spin.setValue(30)
        cancel_from_dashboard = self.window.on_task_start
        with patch.object(self.window, "on_task_start") as start:
            self.startup.start()
            cancel_from_dashboard()
            self.assertFalse(self.startup.timer.isActive())
            self.expire_wait()
        start.assert_not_called()
        self.assertFalse(self.startup.active)

    def test_close_cancels_task_delay(self):
        self.enable("auto_start_tasks")
        self.panel.auto_start_wait_spin.setValue(30)
        with patch.object(self.window, "on_task_start") as start:
            self.startup.start()
            self.window.close()
            fixtures.QApplication.processEvents()
            self.expire_wait()
        start.assert_not_called()
        self.assertFalse(self.startup.timer.isActive())

    def test_empty_queue_does_not_wait(self):
        self.enable("auto_start_tasks")
        self.panel.auto_start_wait_spin.setValue(30)
        with patch.object(self.window, "build_execution_queue", return_value=[]), \
                patch.object(self.window, "on_task_start") as start:
            self.startup.start()
        start.assert_not_called()
        self.assertFalse(self.startup.timer.isActive())
        self.assertIn("선택되지", self.window.ui.logPrintText.toPlainText())

    def test_launch_only_does_not_connect_or_start_tasker(self):
        self.target()
        self.enable("launch_program")
        self.panel.auto_start_wait_spin.setValue(30)
        with patch.object(self.window, "on_task_start") as start:
            self.startup.start()
            self.wait_launch()
        start.assert_not_called()
        self.launch.assert_called_once()
        self.assertFalse(self.startup.active)
        self.window.runtime.initialize.assert_not_called()

    def test_launch_then_wait_and_no_duplicate_launch_task(self):
        self.target()
        self.enable("launch_program", "auto_start_tasks")
        self.launch.return_value = {"pid": 123, "path": "C:/Example/player.exe"}
        with patch.object(self.window, "on_task_start") as start:
            self.startup.start()
            self.wait_launch()
            self.assertTrue(self.startup.timer.isActive())
            start.assert_not_called()
            self.startup.timer.stop()
            self.startup._complete()
            self.startup._complete()
        start.assert_called_once()
        self.assertTrue(self.window._startup_queue_override)
        self.assertFalse(any(entry == PROGRAM_LAUNCH_ENTRY for entry, _ in self.window._startup_queue_override))
        self.assertEqual(self.startup.launch_identity["pid"], 123)

    def test_dashboard_button_cancels_startup_wait_without_closing_target(self):
        self.target()
        self.enable("launch_program", "auto_start_tasks")
        self.startup.start()
        self.wait_launch()
        self.assertEqual(self.window.ui.workStartBtn.text(), "시작 동작 취소")
        self.window.on_task_start()
        self.assertFalse(self.startup.active)
        self.assertFalse(self.startup.timer.isActive())
        self.assertFalse(self.window.isRunning)
        self.assertIn("취소", self.window.ui.logPrintText.toPlainText())

    def test_launch_failure_does_not_start_tasks(self):
        self.enable("launch_program", "auto_start_tasks")
        self.launch.side_effect = OSError("missing target")
        with patch.object(self.window, "on_task_start") as start:
            self.startup.start()
            self.wait_launch()
        start.assert_not_called()
        self.assertIn("실패", self.window.ui.logPrintText.toPlainText())
        self.assertFalse(self.startup.active)

    def test_missing_interface_skips_automatic_execution(self):
        self.enable("launch_program", "auto_start_tasks")
        self.window.runtime.interface_missing = True
        with patch.object(self.window, "on_task_start") as start:
            self.startup.start()
        start.assert_not_called()
        self.launch.assert_not_called()

    def test_close_waits_for_launch_worker_and_cancels_followup(self):
        self.target()
        self.enable("launch_program", "auto_start_tasks")
        entered, release = threading.Event(), threading.Event()
        def launch(*_args, **_kwargs):
            entered.set()
            release.wait(3)
        self.launch.side_effect = launch
        self.addCleanup(release.set)
        with patch.object(self.window, "on_task_start") as start:
            self.startup.start()
            self.assertTrue(entered.wait(1))
            self.window.close()
            self.assertTrue(self.window._close_pending)
            release.set()
            self.wait_launch()
            fixtures.QTest.qWait(20)
        start.assert_not_called()
        self.assertTrue(self.startup.ready_to_close)
        self.assertFalse(self.startup.timer.isActive())


if __name__ == "__main__":
    unittest.main()
