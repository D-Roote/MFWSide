"""Shared Start options, fixed toolbar, discovery races and guarded app lifecycle."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import test_runtime_lifecycle as fixtures
from test_multi_execution import FakeContext, FakeQueue, connection
from PySide6.QtWidgets import QApplication, QFrame, QLabel
from PySide6.QtTest import QTest
from app.monitoring import ConnectionTarget


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
        self.panel.manager.event_channel_factory = FakeQueue
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

    def adb_connection(self, address="127.0.0.1:16480"):
        if not any(preset["name"] == "Adb" for preset in self.panel.presets):
            preset = {"name": "Adb", "type": "Adb", "label": "ADB"}
            self.panel.presets.append(preset)
            self.window.runtime.interface["controller"].append(preset)
            self.window.runtime.resource_config["controller"].append("Adb")
        adb_path = self.window.runtime.user_dir / "adb.exe"
        adb_path.touch()
        data = connection("Adb", address)
        data["preferences"] = {"adb_path": "", "address": address}
        data["target"]["device"] = {
            "adb_path": str(adb_path), "address": address,
            "input_methods": (1 << 64) - 9, "screencap_methods": 64,
            "config": {"extras": {"mumu": {"enable": True, "index": 3}}},
        }
        return data

    def test_auto_discovery_connects_uint64_adb_devices_without_partial_cards(self):
        found = [self.adb_connection(), self.adb_connection("127.0.0.1:16384")]
        worker = MagicMock(result=(found, []), emulators=[])
        with patch.object(self.window.monitor, "prepare_task_start", return_value=False):
            self.panel._discovered(worker, None, self.window.multi_execution_snapshot(include_tasks=False))
        self.assertEqual(len(self.panel.cards), 2)
        self.assertEqual(self.panel.cards_contents.findChildren(QFrame, "multiInstanceCard"),
                         list(self.panel.cards.values()))
        for session in self.panel.manager.sessions.values():
            self.assertEqual(session.state, "checking")
            self.assertEqual(session.process.args[1]["target"]["device"]["input_methods"], (1 << 64) - 9)

    def test_individual_discovery_preserves_uint64_metadata_on_target_change(self):
        first, second = self.adb_connection(), self.adb_connection("127.0.0.1:16384")
        sid = self.panel.manager.add({"preset": "Adb"})
        worker = MagicMock(result=([first, second], []), emulators=[])
        self.panel._discovered(worker, sid, self.window.multi_execution_snapshot(include_tasks=False))
        card = self.panel.cards[sid]
        self.assertEqual(card.emulator_combo.currentText(), "최소화 대상 선택")
        self.assertEqual(card.emulator_combo.currentData(), {})
        card.target_combo.setCurrentIndex(2)
        value = self.panel.manager.sessions[sid].connection
        self.assertEqual(value["target"], second["target"])
        self.assertEqual(value["preferences"]["address"], second["target"]["key"])
        self.assertEqual(value["target"]["device"]["input_methods"], (1 << 64) - 9)

    def test_restored_uint64_device_card_stays_inside_hidden_feature_page(self):
        data = self.adb_connection()
        self.window.ui.dashboardNavButton.click()
        self.panel.manager.restore([{"title": "Restored", "connection": data}])
        QApplication.processEvents()
        self.assertEqual(len(self.panel.cards), 1)
        card = next(iter(self.panel.cards.values()))
        self.assertIs(card.parentWidget(), self.panel.cards_contents)
        self.assertFalse(card.isVisible())
        self.assertFalse(self.panel.findChild(QFrame, "multiToolbar").isVisible())
        self.assertEqual(self.panel.manager.saved_sessions()[0]["connection"], data)

    def test_real_discovery_completion_creates_complete_cards_on_gui_thread(self):
        data = self.adb_connection()
        with patch("app.multiExecutionUI.discover_connections", return_value=([data], [])), \
                patch("maa.toolkit.Toolkit.find_desktop_windows", return_value=[]), \
                patch.object(self.window.monitor, "prepare_task_start", return_value=False):
            self.panel.auto_button.click()
            for _ in range(100):
                if self.panel.discovery_worker is None:
                    break
                QTest.qWait(10)
            self.assertIsNone(self.panel.discovery_worker)
        self.assertEqual(len(self.panel.cards), 1)
        card = next(iter(self.panel.cards.values()))
        self.assertIs(card.thread(), self.fixture.app.thread())
        self.assertEqual(card.current_target(), data["target"])
        self.assertEqual(card.target_combo.currentData(), data["target"]["key"])

    def test_start_page_connection_does_not_add_or_reveal_instances(self):
        data = self.adb_connection()
        metadata = data["target"]["device"]
        device = SimpleNamespace(**metadata)
        target = ConnectionTarget(data["target"]["key"], "MuMu", "Adb", device=device)
        monitor = self.window.monitor
        monitor.service.presets.append({"name": "Adb", "type": "Adb"})
        monitor.preset_name, monitor.targets, monitor.target_key = "Adb", [target], target.key
        self.window.ui.dashboardNavButton.click()
        with patch.object(monitor.service, "connect", return_value=target) as connect:
            monitor.connect_target()
            for _ in range(100):
                if monitor.worker is None:
                    break
                QTest.qWait(10)
            self.assertIsNone(monitor.worker)
        connect.assert_called_once()
        self.assertIn("연결 성공", monitor.status)
        self.assertFalse(self.panel.manager.sessions)
        self.assertFalse(self.panel.cards)
        self.assertFalse(self.panel.isVisible())
        self.assertFalse(self.panel.findChild(QFrame, "multiToolbar").isVisible())

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

    def test_toolbar_omits_notice_row_and_diagnostics_use_existing_log(self):
        toolbar = self.panel.findChild(QFrame, "multiToolbar")
        self.assertEqual(toolbar.layout().count(), 3)
        self.assertFalse(any("연결 확인 후" in label.text() for label in toolbar.findChildren(QLabel)))
        height = toolbar.height()
        self.panel.report_status("연결 대상 확인 오류")
        QApplication.processEvents()
        self.assertEqual(toolbar.height(), height)
        self.assertIn("[다중 실행] 연결 대상 확인 오류", self.window.ui.logPrintText.toPlainText())

    def test_compact_cards_keep_two_rows_and_aligned_controls_in_both_themes(self):
        ids = [self.add(i + 1) for i in range(5)]
        for theme in ("light", "dark"):
            combo = self.window.settings_panel.theme_combo
            combo.setCurrentIndex(combo.findData(theme))
            self.window.resize(1100, 750)
            self.panel.cards_scroll.verticalScrollBar().setValue(0)
            QApplication.processEvents()
            for sid in ids:
                card = self.panel.cards[sid]
                self.assertLessEqual(card.height(), 100)
                self.assertFalse(card.adb_details.isVisible())
                self.assertFalse(card.adb_details_toggle.isVisible())
                self.assertEqual(card.status.x(), card.target_combo.x())
                self.assertLess(abs(card.start_button.mapTo(card, card.start_button.rect().center()).y()
                                    - card.status.mapTo(card, card.status.rect().center()).y()), 3)
                self.assertLess(abs(card.preset_combo.mapTo(card, card.preset_combo.rect().center()).y()
                                    - card.target_combo.mapTo(card, card.target_combo.rect().center()).y()), 3)
            preview_dir = os.environ.get("MAABA_UI_PREVIEW_DIR")
            if preview_dir:
                path = Path(preview_dir)
                path.mkdir(parents=True, exist_ok=True)
                self.assertTrue(self.panel.grab().save(str(path / f"compact_instances_{theme}.png")))

    def test_adb_action_buttons_share_columns_in_both_themes_and_detail_states(self):
        sid = self.panel.manager.add(self.adb_connection())
        card = self.panel.cards[sid]
        pairs = (
            (card.start_button, card.adb_details_toggle),
            (card.stop_button, card.discover_button),
            (card.delete_button, card.check_button),
        )
        for theme in ("light", "dark"):
            combo = self.window.settings_panel.theme_combo
            combo.setCurrentIndex(combo.findData(theme))
            for width in (1000, 1100, 1400):
                self.window.resize(width, 750)
                for expanded in (False, True):
                    with self.subTest(theme=theme, width=width, expanded=expanded):
                        card.adb_details_toggle.setChecked(expanded)
                        QTest.qWait(20)
                        for upper, lower in pairs:
                            self.assertEqual(upper.geometry().left(), lower.geometry().left())
                            self.assertEqual(upper.geometry().right(), lower.geometry().right())
                        self.assertEqual(self.panel.cards_scroll.horizontalScrollBar().maximum(), 0)

    def test_long_names_targets_and_failures_never_enlarge_card(self):
        sid = self.add()
        card, session = self.panel.cards[sid], self.panel.manager.sessions[sid]
        QApplication.processEvents()
        normal_height = card.height()
        card.title.setText("매우 긴 인스턴스 이름 " * 30)
        card.target_combo.setItemText(1, "매우 긴 창 제목과 실행 경로 " * 30)
        session.state = "failed"
        session.result = {"succeeded": False, "message": "연결 오류에 관한 자세한 설명\n" * 40}
        card.refresh()
        QApplication.processEvents()
        self.assertEqual(card.height(), normal_height)
        self.assertEqual(card.status.text(), "실패")
        self.assertIn(session.result["message"], card.status.toolTip())
        self.assertEqual(self.panel.cards_scroll.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(self.panel.cards_contents.width(), self.panel.cards_scroll.viewport().width())

    def test_adb_details_start_collapsed_and_expansion_preserves_connection(self):
        preset = {"name": "Adb", "type": "Adb", "label": "ADB"}
        self.panel.presets.append(preset)
        self.window.runtime.interface["controller"].append(preset)
        self.window.runtime.resource_config["controller"].append("Adb")
        data = connection("Adb", "127.0.0.1:5555")
        data["preferences"] = {"adb_path": "C:/SDK/adb.exe", "address": "127.0.0.1:5555"}
        sid = self.panel.manager.add(data)
        card = self.panel.cards[sid]
        QApplication.processEvents()
        collapsed_height = card.height()
        self.assertEqual(card.emulator_combo.currentText(), "최소화 대상 선택")
        self.assertEqual(card.emulator_combo.currentData(), {})
        self.assertLessEqual(collapsed_height, 100)
        self.assertTrue(card.adb_details_toggle.isVisible())
        self.assertFalse(card.adb_details.isVisible())
        card.adb_details_toggle.click()
        QTest.qWait(20)
        self.assertTrue(card.adb_details.isVisible())
        self.assertGreater(card.height(), collapsed_height)
        self.assertEqual(card.address.text(), "127.0.0.1:5555")
        self.assertEqual(self.panel.manager.sessions[sid].connection, data)
        card.adb_details_toggle.click()
        QTest.qWait(20)
        self.assertFalse(card.adb_details.isVisible())
        self.assertEqual(card.height(), collapsed_height)
        self.assertEqual(self.panel.manager.sessions[sid].connection, data)

    def test_small_workspace_still_fits_long_connection_text(self):
        sid = self.add()
        self.window.resize(1000, 650)
        self.panel.cards[sid].target_combo.setItemText(1, "연결 대상 경로 " * 100)
        QApplication.processEvents()
        self.assertEqual(self.panel.cards_scroll.horizontalScrollBar().maximum(), 0)
        self.assertLessEqual(self.panel.cards_contents.width(), self.panel.cards_scroll.viewport().width())

    def test_connection_status_stays_aligned_with_target_for_adb_and_window_sizes(self):
        sid = self.add()
        preset = {"name": "Adb", "type": "Adb", "label": "ADB"}
        self.panel.presets.append(preset)
        adb_id = self.panel.manager.add(connection("Adb", "127.0.0.1:5555"))
        for width in (1000, 1100, 1400):
            self.window.resize(width, 750)
            for expanded in (False, True):
                self.panel.cards[adb_id].adb_details_toggle.setChecked(expanded)
                QTest.qWait(20)
                for instance_id in (sid, adb_id):
                    card = self.panel.cards[instance_id]
                    self.assertEqual(card.status.x(), card.target_combo.x())
                    self.assertEqual(card.title.x(), card.preset_combo.x())
                    self.assertEqual(card.status.width() + 6 + card.task_preset_combo.width(), card.target_combo.width())
        self.assertEqual(self.panel.cards_scroll.horizontalScrollBar().maximum(), 0)

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
        self.assertIn("invalid input", self.window.ui.logPrintText.toPlainText())

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
        self.assertEqual(set(data["multi_instances"][0]), {"title", "connection", "task_preset"})
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
