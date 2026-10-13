"""Shared Start options, fixed toolbar, discovery races and guarded app lifecycle."""
import json
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from test_multi_execution import FakeContext, connection
from PySide6.QtWidgets import QApplication


class MultiExecutionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.window.runtime.interface_path = self.window.runtime.user_dir / "interface.json"
        self.panel = self.window.multi_panel
        self.panel.manager.context = FakeContext()
        self.panel.manager.desktop_lock = self.panel.manager.context.Lock()
        self.addCleanup(self.panel.manager.timer.stop)
        self.addCleanup(self.discard_processes)
        self.window.show()
        self.window.ui.extraFeaturesNavButton.click()
        QApplication.processEvents()

    def discard_processes(self):
        for session in self.panel.manager.sessions.values():
            session.process = None

    def add(self, value=42):
        data = connection(value=value)
        data["preset"] = "Win32PrintWindow"
        return self.panel.manager.add(data)

    def test_fixed_toolbar_and_scrolling_cards_fit_light_and_dark(self):
        ids = [self.add(i + 1) for i in range(15)]
        for theme in ("light", "dark"):
            combo = self.window.settings_panel.theme_combo
            combo.setCurrentIndex(combo.findData(theme))
            self.window.resize(1100, 650)
            QApplication.processEvents()
            before = self.panel.start_all.mapToGlobal(self.panel.start_all.rect().topLeft())
            self.panel.cards_scroll.verticalScrollBar().setValue(10000)
            QApplication.processEvents()
            self.assertEqual(before, self.panel.start_all.mapToGlobal(self.panel.start_all.rect().topLeft()))
            self.assertGreater(self.panel.cards_scroll.verticalScrollBar().maximum(), 0)
            self.assertEqual(self.panel.cards_scroll.horizontalScrollBar().maximum(), 0)
            self.assertLessEqual(self.panel.cards_contents.width(), self.panel.cards_scroll.viewport().width())
        self.assertEqual(len(self.panel.cards), len(ids))

    def test_one_bulk_snapshot_copies_task_options_and_personal_settings(self):
        ids = [self.add(42), self.add(43)]
        task = self.fixture.find_task_widget(self.window)
        task.selected_options["Test_Mode"] = ["B"]
        expected = self.window.multi_execution_snapshot()
        with patch.object(self.window.monitor, "prepare_task_start", return_value=False):
            self.panel.start_sessions(ids)
        self.panel.manager.timer.stop()
        for sid in ids:
            args = self.panel.manager.sessions[sid].process.args
            for key in ("queue", "settings", "resource", "controller", "program", "minimize", "after_actions"):
                self.assertEqual(args[0][key], expected[key])
        task.selected_options["Test_Mode"] = ["A"]
        self.assertEqual(self.panel.manager.sessions[ids[0]].process.args[0]["queue"], expected["queue"])
        self.assertFalse(self.window.ui.workStartBtn.isEnabled())

    def test_invalid_bulk_target_starts_none(self):
        sid = self.add()
        self.panel.manager.add({"preset": "Win32PrintWindow"})
        with patch.object(self.panel.manager, "start") as start:
            self.panel.start_sessions(list(self.panel.manager.sessions))
        start.assert_not_called()

    def test_invalid_task_inputs_are_reported_without_spawning(self):
        sid = self.add()
        with patch.object(self.window, "build_execution_queue", side_effect=ValueError("invalid input")):
            with patch.object(self.panel.manager, "start") as start:
                self.panel.start_sessions([sid])
        start.assert_not_called()
        self.assertIn("invalid input", self.panel.notice.text())

    def test_connection_check_does_not_depend_on_valid_task_inputs(self):
        sid = self.add()
        with patch.object(self.window, "build_execution_queue", side_effect=ValueError("invalid input")):
            with patch.object(self.window.monitor, "prepare_task_start", return_value=False):
                self.panel.start_sessions([sid], mode="check")
        session = self.panel.manager.sessions[sid]
        self.assertTrue(session.active)
        self.assertFalse(session.process.args[0]["queue"])
        self.assertFalse(session.process.args[0]["after_actions"])

    def test_deleted_discovery_request_never_becomes_auto_add(self):
        sid = self.add()
        worker = MagicMock(result=([self.panel.manager.sessions[sid].connection], []), emulators=[])
        self.panel.manager.remove(sid)
        self.panel._discovered(worker, sid, self.window.multi_execution_snapshot())
        self.assertFalse(self.panel.manager.sessions)

    def test_discovery_locks_card_controls_and_stale_delete_is_safe(self):
        sid = self.add()
        self.panel.discovery_worker = MagicMock()
        self.panel.refresh()
        card = self.panel.cards[sid]
        self.assertFalse(card.preset_combo.isEnabled())
        self.assertFalse(card.start_button.isEnabled())
        self.assertFalse(self.window.ui.workStartBtn.isEnabled())
        self.panel.discovery_worker = None

    def test_persistence_records_connections_without_running_state(self):
        sid = self.add()
        self.panel.manager.sessions[sid].state = "running"
        self.window.save_user_config()
        data = json.loads((self.window.runtime.user_dir / "config/user_config.json").read_text(encoding="utf-8"))
        self.assertEqual(set(data["multi_instances"][0]), {"title", "connection"})
        self.assertEqual(data["multi_instances"][0]["connection"], self.panel.manager.sessions[sid].connection)

    def test_failure_or_cancelled_wave_never_runs_system_actions(self):
        with patch.object(self.window, "_execute_after_actions") as complete:
            self.window._multi_wave_finished([{"succeeded": False}], {"shutdown": True})
            QApplication.processEvents()
        complete.assert_not_called()

    def test_window_close_waits_until_worker_exits(self):
        sid = self.add()
        with patch.object(self.window.monitor, "prepare_task_start", return_value=False):
            self.panel.start_sessions([sid])
        self.panel.manager.timer.stop()
        event = MagicMock()
        self.window.closeEvent(event)
        event.ignore.assert_called_once()
        self.assertTrue(self.panel.manager.sessions[sid].stop_event.is_set())
        self.assertTrue(self.panel.manager.sessions[sid].active)


if __name__ == "__main__":
    unittest.main()
