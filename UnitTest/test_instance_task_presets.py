"""Instance task choices never modify Start, connect real devices or launch apps."""
from copy import deepcopy
import json
from pathlib import Path
import os
from unittest.mock import patch, MagicMock
import unittest

import test_runtime_lifecycle as fixtures
from test_multi_execution import FakeContext, FakeQueue, connection
from PySide6.QtCore import Qt
from app.multiExecution import SessionManager


class InstanceTaskPresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.panel = self.window.multi_panel
        self.manager = self.panel.manager
        self.manager.context = FakeContext()
        self.manager.event_channel_factory = FakeQueue
        self.addCleanup(self.stop)
        self.window.show()
        self.window.ui.extraFeaturesNavButton.click()
        fixtures.QApplication.processEvents()

    def stop(self):
        self.manager.timer.stop()
        self.window.daily_restart.shutdown()
        for session in self.manager.sessions.values():
            session.process = None

    def add(self, number=42):
        value = connection(value=number)
        value["preset"] = "Win32PrintWindow"
        return self.manager.add(value)

    def preset(self, name="일일 작업", case="B"):
        task = self.fixture.find_task_widget(self.window)
        task.selected_options["Test_Mode"] = [case]
        self.assertTrue(self.window._save_task_preset(name))
        return self.window.task_presets[-1]["id"]

    def select(self, sid, preset_id):
        combo = self.panel.cards[sid].task_preset_combo
        combo.setCurrentIndex(combo.findData(preset_id))
        self.assertEqual(self.manager.sessions[sid].task_preset, preset_id)

    def start(self, ids):
        with patch.object(self.window.monitor, "prepare_task_start", return_value=False):
            self.panel.start_sessions(ids)

    def queue(self, sid):
        return self.manager.sessions[sid].process.args[0]["queue"]

    def test_default_current_tasks_and_choice_does_not_modify_start(self):
        sid = self.add()
        self.assertEqual(self.panel.cards[sid].task_preset_combo.currentText(), "현재 작업 목록")
        preset_id = self.preset()
        before = deepcopy(self.window._serialize_task_list())
        self.select(sid, preset_id)
        self.assertEqual(self.window._serialize_task_list(), before)
        self.select(sid, "")
        self.assertEqual(self.window._serialize_task_list(), before)

    def test_batch_uses_each_preset_and_copies_all_shared_settings(self):
        first, second, third = self.add(41), self.add(42), self.add(43)
        preset_b = self.preset("B 작업", "B")
        preset_a = self.preset("A 작업", "A")
        self.select(first, preset_b)
        self.select(second, preset_a)
        expected_shared = self.window.multi_execution_snapshot()
        before = deepcopy(self.window._serialize_task_list())
        self.start([first, second, third])
        self.assertEqual(self.queue(first)[-1][1]["Test_Node"]["next"], ["B"])
        self.assertEqual(self.queue(second)[-1][1]["Test_Node"]["next"], ["A"])
        self.assertEqual(self.queue(third), expected_shared["queue"])
        for session in self.manager.sessions.values():
            for key in ("resource", "controller", "program", "settings", "minimize", "after_actions"):
                self.assertEqual(session.process.args[0][key], expected_shared[key])
        self.assertEqual(self.window._serialize_task_list(), before)

    def test_named_preset_does_not_depend_on_invalid_current_task_inputs(self):
        sid = self.add()
        self.select(sid, self.preset())
        with patch.object(self.window, "build_execution_queue", side_effect=ValueError("invalid current tasks")):
            self.start([sid])
        self.assertTrue(self.manager.sessions[sid].active)

    def test_selection_is_saved_and_old_records_default_to_current_tasks(self):
        sid = self.add()
        preset_id = self.preset()
        self.select(sid, preset_id)
        self.window.save_user_config()
        data = json.loads((self.window.runtime.user_dir / "config/user_config.json").read_text(encoding="utf-8"))
        self.assertEqual(data["multi_instances"][0]["task_preset"], preset_id)
        restored = SessionManager(context=FakeContext())
        restored.restore(data["multi_instances"])
        self.assertEqual(next(iter(restored.sessions.values())).task_preset, preset_id)
        restored = SessionManager(context=FakeContext())
        restored.restore([{"title": "Old", "connection": self.manager.sessions[sid].connection}])
        self.assertEqual(next(iter(restored.sessions.values())).task_preset, "")

    def test_running_instance_cannot_change_preset(self):
        sid = self.add()
        preset_id = self.preset()
        self.start([sid])
        self.assertFalse(self.panel.cards[sid].task_preset_combo.isEnabled())
        with self.assertRaises(ValueError):
            self.manager.set_task_preset(sid, preset_id)

    def test_deleted_preset_is_visible_and_never_silently_runs_other_tasks(self):
        good, missing = self.add(41), self.add(42)
        self.select(missing, self.preset())
        self.assertTrue(self.window._commit_task_presets([], ""))
        card = self.panel.cards[missing]
        self.assertEqual(card.task_preset_combo.currentText(), "사용할 수 없는 프리셋")
        self.assertFalse(card.start_button.isEnabled())
        with patch.object(self.manager, "start") as start:
            self.start([good, missing])
        start.assert_not_called()
        self.select(missing, "")
        self.assertTrue(card.start_button.isEnabled())

    def test_connection_check_does_not_require_saved_preset(self):
        sid = self.add()
        self.manager.set_task_preset(sid, "deleted-preset")
        with patch.object(self.window.monitor, "prepare_task_start", return_value=False):
            self.panel.start_sessions([sid], mode="check")
        self.assertTrue(self.manager.sessions[sid].active)
        self.assertEqual(self.queue(sid), [])

    def test_snapshot_is_frozen_while_waiting_for_monitor_cleanup(self):
        sid = self.add()
        preset_id = self.preset()
        self.select(sid, preset_id)
        callbacks = []
        def prepare(callback):
            callbacks.append(callback)
            return len(callbacks) == 1
        with patch.object(self.window.monitor, "prepare_task_start", side_effect=prepare):
            self.panel.start_sessions([sid])
            self.fixture.find_task_widget(self.window).selected_options["Test_Mode"] = ["A"]
            self.window._write_task_preset("일일 작업", preset_id)
            callbacks[0]()
        self.assertEqual(self.queue(sid)[-1][1]["Test_Node"]["next"], ["B"])

    def test_daily_restart_snapshots_the_instance_preset(self):
        sid = self.add()
        self.select(sid, self.preset())
        expected = self.panel.snapshot_for_session(sid)["queue"]
        self.manager.start(sid, self.window.multi_execution_snapshot())
        target = self.window.runtime.user_dir / "client.exe"
        target.touch()
        self.window.settings_panel.program_path_input.setText(str(target))
        self.window.settings_panel._apply_program_path()
        with patch.object(self.window.daily_restart, "_advance"):
            self.window.daily_restart._begin()
        self.assertEqual(self.window.daily_restart.jobs[0]["id"], sid)
        self.assertEqual(self.window.daily_restart.jobs[0]["snapshot"]["queue"], expected)
        self.window.daily_restart.cancel()

    def test_layout_settings_left_of_discovery_and_preset_half_target_width(self):
        sid = self.add()
        card = self.panel.cards[sid]
        adb = {"name": "Adb", "type": "Adb", "label": "ADB"}
        self.panel.presets.append(adb)
        other = self.manager.add(connection("Adb", "device"))
        for theme in ("light", "dark"):
            self.window.settings_panel.theme_combo.setCurrentIndex(self.window.settings_panel.theme_combo.findData(theme))
            for width in (1000, 1400):
                self.window.resize(width, 750)
                fixtures.QTest.qWait(20)
                for identifier in (sid, other):
                    card = self.panel.cards[identifier]
                    self.assertLessEqual(card.height(), 100)
                    self.assertAlmostEqual(card.task_preset_combo.width() / card.target_combo.width(), 0.5, delta=0.03)
                    self.assertEqual(card.task_preset_combo.geometry().right(), card.target_combo.geometry().right())
                    self.assertLess(card.task_preset_combo.geometry().right(), card.start_button.x())
                card = self.panel.cards[other]
                self.assertEqual(card.adb_details_toggle.text(), "설정")
                self.assertLess(card.adb_details_toggle.x(), card.discover_button.x())
                self.assertEqual(card.adb_details_toggle.width(), card.discover_button.width())
                self.assertEqual(card.start_button.width(), card.discover_button.width())
                self.assertLess(abs(card.adb_details_toggle.y() - card.discover_button.y()), 2)
                self.assertEqual(self.panel.cards_scroll.horizontalScrollBar().maximum(), 0)
            directory = os.environ.get("MAABA_UI_PREVIEW_DIR")
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
                self.panel.grab().save(str(Path(directory) / f"instance_presets_{theme}.png"))

    def test_preset_wheel_is_ignored_until_popup_opens(self):
        sid = self.add()
        self.preset()
        combo = self.panel.cards[sid].task_preset_combo
        event = MagicMock()
        combo.wheelEvent(event)
        event.ignore.assert_called_once()
        self.assertEqual(combo.currentData(), "")
