"""No real game, ADB, window close, native resource load or launch is performed."""
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
import json
import os
import threading
import unittest

import test_runtime_lifecycle as fixtures
from app.adbAppStop import AdbAppStopTarget
from app.dailyRestart import next_daily_restart, restart_launch_entry, restart_android_app, restart_windows_program, close_window_and_wait
from app.afterActions import WindowTarget
from app.monitoring import ConnectionTarget
from app.startupSettings import normalize_startup_config
from PySide6.QtCore import QTime


def snapshot():
    return {"interface": {"controller": [{"name": "Desktop", "type": "Win32", "win32": {}}]},
            "resource": {"path": ["./"]}, "interface_path": "C:/Example/interface.json", "user_dir": "C:/Example/user",
            "controller": {"name": "Desktop", "type": "Win32"},
            "program": {"resolved_path": "C:/Example/client.exe"}, "minimize": True,
            "queue": [("Main", {})]}


class DailyScheduleTests(unittest.TestCase):
    def test_offset_and_next_day(self):
        now = datetime(2026, 10, 20, 3, 59, 50)
        self.assertEqual(next_daily_restart(now, "04:00"), datetime(2026, 10, 20, 4, 1))
        self.assertEqual(next_daily_restart(now.replace(hour=4, minute=1), "04:00"), datetime(2026, 10, 21, 4, 1))

    def test_midnight_rollover_and_month_end(self):
        self.assertEqual(next_daily_restart(datetime(2026, 10, 31, 23, 59), "23:59"), datetime(2026, 11, 1))

    def test_time_normalization_and_default(self):
        self.assertEqual(normalize_startup_config(None)["daily_restart_time"], "00:00")
        for value in ("4:00", "24:00", "04:60", None, 100, True, " 04:00"):
            self.assertEqual(normalize_startup_config({"daily_restart_time": value})["daily_restart_time"], "00:00")
        self.assertEqual(normalize_startup_config({"daily_restart_time": "23:59"})["daily_restart_time"], "23:59")


class RestartBackendTests(unittest.TestCase):
    def resource(self, *entries):
        nodes = {str(index): {"action": {"type": "StartApp", "param": {"package": entry}}}
                 for index, entry in enumerate(entries)}
        resource = MagicMock(node_list=list(nodes))
        resource.get_node_data.side_effect = nodes.get
        return resource

    def test_uses_normalized_startapp_package_and_overrides(self):
        resource = self.resource("com.example.client", "com.example.client/.Main")
        override = {"Main": {"next": ["Login"]}}
        self.assertEqual(restart_launch_entry(resource, [("Main", override)]), "com.example.client/.Main")
        resource.override_pipeline.assert_called_once_with(override)

    def test_ambiguous_or_missing_package_is_not_guessed(self):
        for entries in ([], ["com.a", "com.b"], ["com.a/.One", "com.a/.Two"], ["com.a;evil"]):
            with self.subTest(entries=entries), self.assertRaises(RuntimeError):
                restart_launch_entry(self.resource(*entries), [])

    def test_disabled_launch_nodes_are_not_targets(self):
        resource = MagicMock(node_list=["Disabled"])
        resource.get_node_data.return_value = {"enabled": False, "action": {"type": "StartApp", "param": {"package": "com.a"}}}
        with self.assertRaises(RuntimeError):
            restart_launch_entry(resource, [])

    def test_adb_restarts_only_selected_package_not_player_or_other_apps(self):
        target = AdbAppStopTarget("adb.exe", "127.0.0.1:1", "boot", "0")
        backend = MagicMock()
        backend.capture.return_value = target
        backend._adb.side_effect = ["com.example.client/.Main", "", "Status: ok"]
        connection = {"target": {"kind": "Adb", "key": target.serial}}
        with patch("app.dailyRestart.load_restart_launch_entry", return_value="com.example.client"), \
                patch("app.dailyRestart.AdbAppStopBackend", return_value=backend), \
                patch("app.dailyRestart.wait_seconds") as wait, \
                patch("app.dailyRestart.request_program_launch") as launch:
            self.assertEqual(restart_android_app(snapshot(), connection, lambda: False), connection)
        commands = [call.args[1:] for call in backend._adb.call_args_list]
        self.assertEqual(commands[1], ("shell", "am", "force-stop", "--user", "0", "com.example.client"))
        self.assertEqual(commands[2], ("shell", "am", "start", "-W", "--user", "0", "-n", "com.example.client/.Main"))
        self.assertEqual(backend._verify_device.call_count, 2)
        launch.assert_not_called()
        wait.assert_called_once()

    def test_adb_does_not_stop_when_activity_cannot_be_resolved(self):
        backend = MagicMock()
        backend._adb.return_value = "No activity found"
        with patch("app.dailyRestart.load_restart_launch_entry", return_value="com.example.client"), \
                patch("app.dailyRestart.AdbAppStopBackend", return_value=backend), self.assertRaises(RuntimeError):
            restart_android_app(snapshot(), {"target": {"key": "device"}}, lambda: False)
        self.assertEqual(backend._adb.call_count, 1)

    def test_adb_device_identity_change_prevents_force_stop(self):
        backend = MagicMock()
        backend._verify_device.side_effect = RuntimeError("device changed")
        with patch("app.dailyRestart.load_restart_launch_entry", return_value="com.example.client/.Main"), \
                patch("app.dailyRestart.AdbAppStopBackend", return_value=backend), self.assertRaises(RuntimeError):
            restart_android_app(snapshot(), {"target": {"key": "device"}}, lambda: False)
        backend._adb.assert_not_called()

    def test_windows_waits_for_exit_before_launch_and_refreshes_target(self):
        old = ConnectionTarget("1", "Old", "Win32", hwnd=1, pid=11)
        new = ConnectionTarget("2", "New", "Win32", hwnd=2, pid=22)
        service = MagicMock()
        service.discover.side_effect = [[old], [new]]
        events = []
        with patch("app.dailyRestart.MonitoringService", return_value=service), \
                patch("app.dailyRestart.close_window_and_wait", side_effect=lambda *_: events.append("closed")), \
                patch("app.dailyRestart.request_program_launch", side_effect=lambda *_, **__: events.append("launch")), \
                patch("app.dailyRestart.wait_seconds", side_effect=lambda *_: events.append("wait")):
            result = restart_windows_program(snapshot(), {}, lambda: False)
        self.assertEqual(events, ["closed", "launch", "wait"])
        self.assertEqual(result["target"]["hwnd"], 2)

    def test_windows_multiple_targets_are_not_closed(self):
        service = MagicMock()
        service.discover.return_value = [MagicMock(), MagicMock()]
        with patch("app.dailyRestart.MonitoringService", return_value=service), \
                patch("app.dailyRestart.close_window_and_wait") as close, \
                patch("app.dailyRestart.request_program_launch") as launch, self.assertRaises(RuntimeError):
            restart_windows_program(snapshot(), {}, lambda: False)
        close.assert_not_called()
        launch.assert_not_called()

    def test_windows_shutdown_timeout_prevents_relaunch(self):
        service = MagicMock()
        service.discover.return_value = [ConnectionTarget("1", "Old", "Win32", hwnd=1, pid=11)]
        with patch("app.dailyRestart.MonitoringService", return_value=service), \
                patch("app.dailyRestart.close_window_and_wait", side_effect=RuntimeError("timeout")), \
                patch("app.dailyRestart.request_program_launch") as launch, self.assertRaises(RuntimeError):
            restart_windows_program(snapshot(), {}, lambda: False)
        launch.assert_not_called()

    def test_windows_close_waits_on_exact_process_handle_and_always_releases_it(self):
        kernel = MagicMock()
        kernel.OpenProcess.return_value = 42
        kernel.WaitForSingleObject.side_effect = [258, 0]
        target = WindowTarget(1, 11, "C:/Example/client.exe")
        backend = MagicMock()
        with patch("app.dailyRestart.ctypes.WinDLL", return_value=kernel), \
                patch("app.dailyRestart.capture_window_target", return_value=target), \
                patch("app.dailyRestart.WindowsAfterActionBackend", return_value=backend):
            close_window_and_wait(1, lambda: False)
        kernel.OpenProcess.assert_called_once_with(0x00100000, False, 11)
        backend.close_program.assert_called_once_with(target)
        self.assertEqual(kernel.WaitForSingleObject.call_count, 2)
        kernel.CloseHandle.assert_called_once_with(42)
        kernel.TerminateProcess.assert_not_called()

    def test_windows_cancellation_never_sends_close_and_releases_handle(self):
        kernel = MagicMock()
        kernel.OpenProcess.return_value = 42
        backend = MagicMock()
        with patch("app.dailyRestart.ctypes.WinDLL", return_value=kernel), \
                patch("app.dailyRestart.capture_window_target", return_value=WindowTarget(1, 11, "app.exe")), \
                patch("app.dailyRestart.WindowsAfterActionBackend", return_value=backend), self.assertRaises(RuntimeError):
            close_window_and_wait(1, lambda: True)
        backend.close_program.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(42)


class DailyRestartUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.panel = self.window.settings_panel
        self.daily = self.window.daily_restart
        self.now = datetime(2026, 10, 20, 3, 59)
        self.daily.clock = lambda: self.now
        self.addCleanup(self.stop)
        self.restart_patch = patch("app.dailyRestartUI.restart_target", side_effect=lambda _s, c, _x: c)
        self.restart = self.restart_patch.start()
        self.addCleanup(self.restart_patch.stop)
        prepare = patch.object(self.window.monitor, "prepare_task_start", return_value=False)
        prepare.start()
        self.addCleanup(prepare.stop)

    def stop(self):
        self.daily.shutdown()
        if self.daily.worker is not None:
            self.daily.worker.wait(5000)
        fixtures.QApplication.processEvents()

    def enable(self):
        path = self.window.runtime.user_dir / "client.exe"
        path.touch()
        self.panel.program_path_input.setText(str(path))
        self.panel._apply_program_path()
        self.panel.daily_restart_time.setTime(QTime(4, 0))
        self.panel.startup_checkboxes["daily_restart"].setChecked(True)

    def finish(self):
        for _ in range(200):
            if not self.daily.active:
                return
            fixtures.QTest.qWait(10)
        self.fail("daily worker did not finish")

    def test_default_disabled_and_path_gated(self):
        self.assertIsNone(self.daily.next_run)
        self.assertFalse(self.panel.startup_checkboxes["daily_restart"].isEnabled())
        self.enable()
        self.assertEqual(self.daily.next_run, datetime(2026, 10, 20, 4, 1))
        saved = json.loads(self.panel.store.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["startup"]["daily_restart_time"], "04:00")
        self.assertTrue(saved["startup"]["daily_restart"])
        self.panel._apply_program_path()
        self.assertFalse(self.panel.startup_checkboxes["daily_restart"].isChecked())
        self.assertIsNone(self.daily.next_run)

    def test_daily_time_ignores_wheel_and_fits_themed_minimum_width(self):
        event = MagicMock()
        self.panel.daily_restart_time.wheelEvent(event)
        event.ignore.assert_called_once()
        self.enable()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        self.window.resize(self.window.minimumWidth(), 820)
        self.window.show()
        self.panel.navigation.setCurrentRow(self.panel.section_order().index("program"))
        for theme in ("light", "dark"):
            self.panel.theme_combo.setCurrentIndex(self.panel.theme_combo.findData(theme))
            fixtures.QApplication.processEvents()
            self.assertEqual(self.panel.detail_scroll.horizontalScrollBar().maximum(), 0)
            self.assertGreater(self.panel.daily_restart_time.width(), 60)
            directory = os.environ.get("MAABA_UI_PREVIEW_DIR")
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
                self.panel.grab().save(str(Path(directory) / f"daily_restart_{theme}.png"))
    def test_once_per_day_and_excludes_launch_task_without_touching_selection(self):
        self.enable()
        before = self.window._serialize_task_list()
        self.now = datetime(2026, 10, 20, 4, 1)
        with patch.object(self.window, "on_task_start") as start:
            self.daily.tick()
            self.finish()
            self.daily.tick()
        self.restart.assert_called_once()
        start.assert_called_once()
        self.assertFalse(any(entry == "__LaunchProgram" for entry, _ in self.window._startup_queue_override))
        self.assertEqual(self.window._serialize_task_list(), before)
        self.assertEqual(self.daily.next_run, datetime(2026, 10, 21, 4, 1))

    def test_unrelated_settings_save_does_not_skip_due_restart(self):
        self.enable()
        original = self.daily.next_run
        self.now = datetime(2026, 10, 20, 4, 2)
        self.panel.auto_start_wait_spin.setValue(20)
        self.assertEqual(self.daily.next_run, original)

    def test_waits_for_task_and_stop_worker_cleanup_and_cancel_prevents_launch(self):
        self.enable()
        self.window.worker = MagicMock()
        with patch.object(self.window, "on_task_stop") as stop:
            self.daily._begin()
            stop.assert_called_once()
            self.assertIsNone(self.daily.worker)
            self.daily.cancel()
            self.window.worker = None
            self.daily.tick()
        self.restart.assert_not_called()

    def test_monitor_cleanup_must_finish_before_restart(self):
        self.enable()
        with patch.object(self.window.monitor, "prepare_task_start", return_value=True):
            self.daily._begin()
        self.assertEqual(self.daily.phase, "monitor")
        self.restart.assert_not_called()
        self.daily.cancel()
        self.daily._start_worker()
        self.restart.assert_not_called()

    def test_missing_selected_tasks_prevents_target_restart(self):
        self.enable()
        with patch.object(self.window, "multi_execution_snapshot", return_value={"queue": []}):
            self.daily._begin()
        self.restart.assert_not_called()
        self.assertFalse(self.daily.active)

    def test_close_prevents_scheduled_restart(self):
        self.enable()
        self.daily.shutdown()
        self.now = datetime(2026, 10, 20, 4, 1)
        self.daily.tick()
        self.restart.assert_not_called()

    def test_close_waits_for_restart_worker_and_never_starts_tasks_afterwards(self):
        self.enable()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def restarting(_snapshot, connection, _cancel):
            entered.set()
            release.wait(3)
            return connection
        self.restart.side_effect = restarting
        with patch.object(self.window, "on_task_start") as start:
            self.daily._begin()
            self.assertTrue(entered.wait(1))
            self.window.close()
            self.assertTrue(self.window._close_pending)
            self.assertFalse(self.daily.ready_to_close)
            self.assertTrue(self.daily.cancelled)
            release.set()
            self.finish()
            fixtures.QApplication.processEvents()
        start.assert_not_called()
        self.assertTrue(self.daily.ready_to_close)

    def test_running_instances_are_snapshotted_and_resumed_individually(self):
        self.enable()
        from test_multi_execution import FakeContext, FakeQueue, connection
        manager = self.window.multi_panel.manager
        manager.context = FakeContext()
        manager.event_channel_factory = FakeQueue
        self.addCleanup(manager.timer.stop)
        ids = []
        for number in (41, 42):
            value = connection(value=number)
            value["preset"] = "Win32PrintWindow"
            sid = manager.add(value)
            manager.start(sid, self.window.multi_execution_snapshot())
            ids.append(sid)
        with patch.object(self.window.multi_panel, "start_sessions") as start, \
                patch.object(self.window, "on_task_start") as base_start:
            self.daily._begin()
            self.assertEqual([job["id"] for job in self.daily.jobs], ids)
            self.restart.assert_not_called()
            for session in manager.sessions.values():
                self.assertTrue(session.stop_event.is_set())
                session.process = None
            self.daily.tick()
            self.finish()
        self.assertEqual(start.call_count, 2)
        base_start.assert_not_called()
        self.assertEqual(self.restart.call_count, 2)

    def test_failure_does_not_start_tasks_or_retry_on_next_tick(self):
        self.enable()
        self.restart.side_effect = RuntimeError("restart failed")
        self.now = datetime(2026, 10, 20, 4, 1)
        with patch.object(self.window, "on_task_start") as start:
            self.daily.tick()
            self.finish()
            self.daily.tick()
        start.assert_not_called()
        self.restart.assert_called_once()
        self.assertIn("restart failed", self.window.ui.logPrintText.toPlainText())

    def test_preview_keeps_stream_intent_but_never_captures_during_restart(self):
        self.daily.active = True
        self.window.monitor.streaming = True
        with patch.object(self.window.monitor, "_request") as request:
            self.window.monitor.capture_frame()
        request.assert_not_called()
        self.assertTrue(self.window.monitor.streaming)
        self.assertTrue(self.window.monitor.preview_timer.isActive())
        self.window.monitor.streaming = False
        self.window.monitor.preview_timer.stop()
        self.daily.active = False
