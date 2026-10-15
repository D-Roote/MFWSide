"""Mock-only shutdown routing: never invoke a real power API or subprocess."""
import json
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

import test_after_actions as completion_fixtures
import test_runtime_lifecycle as fixtures
from PySide6.QtGui import QCloseEvent


class ShutdownCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = completion_fixtures.CompletionUITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window, self.backend = self.fixture.window, self.fixture.backend
        self.window.after_actions.update("close_maa", True)
        self.window.after_actions.update("system_action", "shutdown")
        self.log_path = self.window.runtime.user_dir / "debug" / "after_action.log"
        self.window.show()

    def finish(self, succeeded=True):
        self.fixture.start(succeeded)
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()

    def assert_shutdown_once_without_helper(self):
        self.backend.system_action.assert_called_once_with("shutdown")
        self.backend.defer_system_action.assert_not_called()
        self.assertEqual(self.window._deferred_system_action, "")

    def test_shutdown_follows_save_and_all_component_cleanup_before_window_close(self):
        order = []
        save = self.window.save_user_config
        monitor_shutdown = self.window.monitor.shutdown
        multi_shutdown = self.window.multi_panel.shutdown

        def save_config():
            save()
            order.append("saved")

        def close_monitor():
            monitor_shutdown()
            order.append("monitor")

        def close_multi():
            multi_shutdown()
            order.append("multi")

        def request_shutdown(action):
            self.assertEqual(action, "shutdown")
            self.assertTrue(self.window.isVisible())
            self.assertIsNone(self.window.worker)
            self.assertIsNone(self.window.stop_worker)
            self.assertIsNone(self.window._adb_app_stop_worker)
            self.assertTrue(self.window.monitor.ready_to_close)
            self.assertTrue(self.window.multi_panel.ready_to_close)
            saved = json.loads(self.fixture.path.read_text(encoding="utf-8"))
            self.assertTrue(saved["after_actions"]["close_maa"])
            self.assertEqual(saved["after_actions"]["system_action"], "shutdown")
            order.append("shutdown")

        self.backend.system_action.side_effect = request_shutdown
        with patch.object(self.window, "save_user_config", side_effect=save_config), \
                patch.object(self.window.monitor, "shutdown", side_effect=close_monitor), \
                patch.object(self.window.multi_panel, "shutdown", side_effect=close_multi):
            self.finish()
        self.assertEqual(order[-4:], ["saved", "monitor", "multi", "shutdown"])
        self.assert_shutdown_once_without_helper()
        self.assertFalse(self.window.isVisible())
        self.window.closeEvent(QCloseEvent())
        self.assert_shutdown_once_without_helper()

    def test_runtime_and_stop_callbacks_both_finish_before_shutdown(self):
        self.fixture.start()
        self.window.stop_worker = MagicMock(succeeded=True, result_message="released")
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()
        self.backend.system_action.assert_not_called()
        self.backend.defer_system_action.assert_not_called()
        self.window.on_stop_worker_finished()
        fixtures.QApplication.processEvents()
        self.assert_shutdown_once_without_helper()
        self.window.on_task_finished()
        self.window.on_stop_worker_finished()
        fixtures.QApplication.processEvents()
        self.assert_shutdown_once_without_helper()

    def assert_component_cleanup_delays_shutdown(self, component):
        self.fixture.start()
        with patch.object(type(component), "ready_to_close", new_callable=PropertyMock, return_value=False) as ready:
            self.window.on_task_finished()
            fixtures.QApplication.processEvents()
            self.assertTrue(self.window._close_pending)
            self.assertTrue(self.window.isVisible())
            self.assertEqual(self.window._deferred_system_action, "shutdown")
            self.backend.system_action.assert_not_called()
            self.backend.defer_system_action.assert_not_called()
            ready.return_value = True
            component.shutdown_ready.emit()
            fixtures.QApplication.processEvents()
            self.assert_shutdown_once_without_helper()
            self.assertFalse(self.window.isVisible())

    def test_monitor_cleanup_must_finish_before_shutdown(self):
        self.assert_component_cleanup_delays_shutdown(self.window.monitor)

    def test_multi_instance_cleanup_must_finish_before_shutdown(self):
        self.assert_component_cleanup_delays_shutdown(self.window.multi_panel)

    def test_frozen_build_uses_existing_nonforced_api_without_launching_python(self):
        api_fixture = completion_fixtures.BackendTests()
        api_fixture.setUp()
        backend = api_fixture.backend
        backend.defer_system_action = MagicMock(side_effect=AssertionError("helper must not run"))
        self.window.after_action_backend = backend
        with patch("sys.frozen", True, create=True), patch("app.afterActions.subprocess.Popen") as launch:
            self.finish()
        backend.user32.ExitWindowsEx.assert_called_once_with(8, 0x80040000)
        self.assertEqual(backend.advapi32.AdjustTokenPrivileges.call_count, 2)
        backend.kernel32.OpenProcess.assert_not_called()
        backend.defer_system_action.assert_not_called()
        launch.assert_not_called()
        self.assertFalse(self.window.isVisible())

    def test_api_failure_is_recorded_once_and_does_not_block_requested_app_exit(self):
        self.backend.system_action.side_effect = OSError("power permission denied")
        self.finish()
        self.assert_shutdown_once_without_helper()
        self.assertEqual(self.window.ui.logPrintText.toPlainText().count("power permission denied"), 1)
        self.assertEqual(self.log_path.read_text(encoding="utf-8").count("power permission denied"), 1)
        self.assertFalse(self.window.isVisible())
        self.window.closeEvent(QCloseEvent())
        self.assert_shutdown_once_without_helper()
        self.assertEqual(self.log_path.read_text(encoding="utf-8").count("power permission denied"), 1)

    def test_shutdown_without_app_exit_keeps_existing_direct_request_behavior(self):
        self.window.after_actions.update("close_maa", False)
        self.finish()
        self.assert_shutdown_once_without_helper()
        self.assertFalse(self.window._closing)
        self.assertTrue(self.window.isVisible())

    def test_reentrant_close_during_shutdown_does_not_repeat_power_request(self):
        self.backend.system_action.side_effect = lambda _action: self.window.closeEvent(QCloseEvent())
        self.finish()
        self.assert_shutdown_once_without_helper()
        self.assertFalse(self.window.isVisible())

    def test_error_log_write_failure_does_not_retry_shutdown_or_block_app_exit(self):
        self.backend.system_action.side_effect = OSError("power permission denied")
        with patch("app.winUI.write_after_action_log", side_effect=OSError("read-only log directory")):
            self.finish()
        self.assert_shutdown_once_without_helper()
        text = self.window.ui.logPrintText.toPlainText()
        self.assertEqual(text.count("power permission denied"), 1)
        self.assertEqual(text.count("read-only log directory"), 1)
        self.assertFalse(self.window.isVisible())

    def test_failed_run_never_requests_shutdown_or_helper(self):
        self.finish(succeeded=False)
        self.backend.system_action.assert_not_called()
        self.backend.defer_system_action.assert_not_called()
        self.assertFalse(self.window._closing)

    def test_manual_stop_never_requests_shutdown_or_helper(self):
        self.fixture.start()
        worker = MagicMock(succeeded=True, result_message="stopped")
        with patch("app.winUI.StopWorker", return_value=worker):
            self.window.on_task_stop()
        self.window.on_stop_worker_finished()
        self.window.on_task_finished()
        fixtures.QApplication.processEvents()
        self.backend.system_action.assert_not_called()
        self.backend.defer_system_action.assert_not_called()
        self.assertFalse(self.window._closing)

    def test_user_close_cancels_already_queued_shutdown_completion(self):
        self.fixture.start()
        self.window.on_task_finished()
        self.window.close()
        fixtures.QApplication.processEvents()
        self.backend.system_action.assert_not_called()
        self.backend.defer_system_action.assert_not_called()
        self.assertEqual(self.window._deferred_system_action, "")


if __name__ == "__main__":
    unittest.main()
