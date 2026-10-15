"""Capture real execution identity, never the UI's later connection selection."""
from dataclasses import asdict
from queue import Queue
import threading
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from test_multi_execution import connection, snapshot
from app.adbAppStop import AdbAppStopTarget
from app.multiExecution import session_process
from app.winUI import RuntimeWorker


class AdbAppStopRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = MagicMock(is_adb=True)
        self.runtime.initialize.return_value = (True, "connected")
        self.runtime.run_task.return_value = (True, "completed")
        self.runtime.release_session.return_value = (True, "released")
        self.runtime._adb_execution_connection = connection("Adb", "actual-device")
        self.target = AdbAppStopTarget("fake-adb.exe", "actual-device",
                                       "11111111-1111-4111-8111-111111111111", "10")

    def test_main_capture_is_before_tasks_and_cleanup_and_uses_runtime_connection(self):
        order = []
        self.runtime.run_task.side_effect = lambda *args, **kwargs: (order.append("run") or (True, "done"))
        self.runtime.release_session.side_effect = lambda: (order.append("release") or (True, "released"))
        def capture(value, cancelled):
            self.assertIs(value, self.runtime._adb_execution_connection)
            self.assertFalse(cancelled())
            order.append("capture")
            return self.target
        worker = RuntimeWorker(self.runtime, [("Task", {})], completion_actions={"close_app": True})
        with patch("app.winUI.capture_adb_app_stop_target", side_effect=capture):
            worker.run()
        self.assertEqual(order, ["capture", "run", "release"])
        self.assertEqual(worker.completion_target, self.target)
        self.assertTrue(worker.succeeded)

    def test_capture_failure_is_safe_and_does_not_prevent_pipeline_execution(self):
        worker = RuntimeWorker(self.runtime, [("Task", {})], completion_actions={"close_app": True})
        with patch("app.winUI.capture_adb_app_stop_target", side_effect=RuntimeError("cannot identify")):
            worker.run()
        self.assertTrue(worker.succeeded)
        self.assertIsNone(worker.completion_target)
        self.runtime.run_task.assert_called_once()

    def test_disabled_or_win32_execution_never_queries_adb_identity(self):
        with patch("app.winUI.capture_adb_app_stop_target") as capture:
            RuntimeWorker(self.runtime, [("Task", {})]).run()
            self.runtime.is_adb = False
            RuntimeWorker(self.runtime, [("Task", {})], completion_actions={"close_app": True}).run()
        capture.assert_not_called()

    def test_multi_process_returns_own_app_target_as_plain_ipc_data(self):
        data = snapshot()
        data["after_actions"] = {"close_app": True}
        data["session_id"] = "mock-session"
        events = Queue()
        with patch("app.multiExecution.SessionRuntime", return_value=self.runtime), \
                patch("app.crashDiagnostics.enable_fault_log"), \
                patch("maa.toolkit.Toolkit.init_option", return_value=True), \
                patch("app.adbAppStop.capture_adb_app_stop_target", return_value=self.target) as capture:
            session_process(data, connection("Adb", "actual-device"), "run", threading.Event(), events, threading.Lock())
        result = next(value for kind, value in list(events.queue) if kind == "result")
        self.assertTrue(result["succeeded"], result)
        self.assertEqual(result["adb_app_stop_target"], asdict(self.target))
        self.assertIs(capture.call_args.args[0], self.runtime._adb_execution_connection)

    def test_connection_check_never_captures_or_stops_apps(self):
        self.runtime._create_controller.return_value = (True, "created")
        self.runtime._execute_controller.return_value = (True, "connected")
        data = snapshot()
        data["after_actions"] = {"close_app": True}
        data["session_id"] = "mock-session"
        events = Queue()
        with patch("app.multiExecution.SessionRuntime", return_value=self.runtime), \
                patch("app.crashDiagnostics.enable_fault_log"), \
                patch("maa.toolkit.Toolkit.init_option", return_value=True), \
                patch("app.adbAppStop.capture_adb_app_stop_target") as capture:
            session_process(data, connection("Adb", "actual-device"), "check", threading.Event(), events, threading.Lock())
        result = next(value for kind, value in list(events.queue) if kind == "result")
        self.assertTrue(result["succeeded"], result)
        capture.assert_not_called()
        self.runtime.run_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
