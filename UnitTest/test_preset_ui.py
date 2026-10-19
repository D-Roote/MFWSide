"""Task preset persistence, edit policy and minimum-width dashboard layout."""
from copy import deepcopy
import json
from unittest.mock import patch
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtWidgets import QApplication, QComboBox, QFrame, QMessageBox, QWidget
from app.winUI import MainWindow, PROGRAM_LAUNCH_TASK_NAME
from app.storageManagement import reset_settings_files


class PresetUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.panel = self.window.preset_panel
        self.window.settings_panel.program_launch_checkbox.setChecked(False)

    def task_widget(self, row=0):
        task_list = self.window.option_list_widget
        return task_list.itemWidget(task_list.item(row))

    def read_config(self):
        return json.loads((self.window.runtime.user_dir / "config/user_config.json").read_text(encoding="utf-8"))

    def save_preset(self, name="Daily"):
        self.assertTrue(self.window._save_task_preset(name))
        return self.window.task_presets[-1]["id"]

    def test_tab_switch_preserves_existing_detail_controls_and_gear_returns(self):
        window = self.window
        window.show_sub_cases(self.task_widget())
        combo = window.ui.scrollSettingContents.findChild(QComboBox)
        combo.setCurrentIndex(1)
        for _ in range(3):
            window.preset_tab_button.click()
            self.assertIs(window.option_pages.currentWidget(), self.panel)
            self.assertTrue(window.preset_tab_button.isChecked())
            window.detail_tab_button.click()
            self.assertIs(window.ui.scrollSettingContents.findChild(QComboBox), combo)
            self.assertEqual(combo.currentIndex(), 1)
        window.preset_tab_button.click()
        window.show_sub_cases(self.task_widget())
        self.assertEqual(window.option_pages.currentIndex(), 0)
        self.assertTrue(window.detail_tab_button.isChecked())
        window.preset_tab_button.click()
        window.show_after_actions()
        self.assertEqual(window.option_pages.currentIndex(), 0)

    def test_save_restores_after_restart_without_automatically_applying(self):
        window = self.window
        identifier = self.save_preset()
        self.task_widget().checkbox.setChecked(False)
        self.assertTrue(window.save_user_config())
        runtime = window.runtime
        with patch("app.winUI.AppRuntime", return_value=runtime):
            reopened = MainWindow()
        self.addCleanup(reopened.deleteLater)
        self.assertEqual(reopened.task_presets, window.task_presets)
        self.assertEqual(reopened.preset_panel.selected_id(), identifier)
        widget = reopened.option_list_widget.itemWidget(reopened.option_list_widget.item(0))
        self.assertFalse(widget.is_checked())
        runtime.initialize.assert_not_called()
        runtime.run_tasks.assert_not_called()

    def test_apply_restores_duplicate_order_checked_and_options_only(self):
        window = self.window
        first = self.task_widget()
        first.selected_options["Test_Mode"] = ["B"]
        first.selected_options["Test_Input"]["Chapter"] = "8"
        window.add_task_widget(window.runtime.interface["task"][0], False,
                               {"Test_Mode": ["A"], "Test_Dropdown": ["Second"]})
        expected = window._serialize_task_list()
        identifier = self.save_preset()
        settings = deepcopy(window.settings_panel.config)
        actions = deepcopy(window.after_actions.saved)
        first.checkbox.setChecked(False)
        first.selected_options["Test_Mode"] = ["A"]
        window.preset_tab_button.click()
        self.assertTrue(window._apply_task_preset(identifier))
        self.assertEqual(window._serialize_task_list(), expected)
        self.assertEqual(window.settings_panel.config, settings)
        self.assertEqual(window.after_actions.saved, actions)
        self.assertIs(window.option_pages.currentWidget(), self.panel)
        self.assertEqual(self.read_config()["tasks"], expected)
        window.runtime.initialize.assert_not_called()
        self.assertTrue(self.panel.status.isHidden())

    def test_password_is_excluded_when_saving_and_loading(self):
        window = self.window
        definition = window.runtime.interface["option"]["Test_Input"]
        definition["inputs"].append({"name": "Secret", "password": True})
        self.task_widget().selected_options["Test_Input"]["Secret"] = "private-value"
        self.save_preset()
        data = self.read_config()
        self.assertNotIn("private-value", json.dumps(data))
        self.assertNotIn("Secret", data["task_presets"]["items"][0]["tasks"][0]["selected_options"]["Test_Input"])

    def test_default_names_and_duplicate_names(self):
        self.save_preset("")
        self.save_preset("  ")
        self.assertEqual([preset["name"] for preset in self.window.task_presets], ["프리셋 1", "프리셋 2"])
        self.save_preset("Daily")
        self.assertFalse(self.window._save_task_preset("DAILY"))
        self.assertEqual(len(self.window.task_presets), 3)
        self.assertTrue(self.panel.status.property("error"))

    def test_save_button_and_enter_follow_name_field(self):
        self.panel.name_input.setText("Saved by button")
        self.panel.save_button.click()
        self.assertEqual(self.window.task_presets[0]["name"], "Saved by button")
        self.assertTrue(self.panel.status.isHidden())
        self.panel.name_input.setText("Saved by Enter")
        self.panel.name_input.returnPressed.emit()
        self.assertEqual(self.window.task_presets[1]["name"], "Saved by Enter")
        self.assertTrue(self.panel.status.isHidden())

    def test_overwrite_and_delete_confirm_and_keep_current_tasks(self):
        identifier = self.save_preset()
        old = deepcopy(self.window.task_presets)
        self.task_widget().checkbox.setChecked(False)
        with patch("app.winUI.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(self.window._replace_task_preset(identifier, "Changed"))
            self.assertFalse(self.window._delete_task_preset(identifier))
        self.assertEqual(self.window.task_presets, old)
        with patch("app.winUI.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.assertTrue(self.window._replace_task_preset(identifier, "Changed"))
            self.assertEqual(self.window.task_presets[0]["id"], identifier)
            self.assertEqual(self.window.task_presets[0]["name"], "Changed")
            self.assertFalse(self.window.task_presets[0]["tasks"][0]["checked"])
            current = self.window._serialize_task_list()
            self.assertTrue(self.window._delete_task_preset(identifier))
        self.assertEqual(self.window._serialize_task_list(), current)
        self.assertEqual(self.read_config()["task_presets"]["items"], [])
        self.assertFalse(self.panel.apply_button.isEnabled())

    def test_failed_save_rolls_back_library_and_reports_error(self):
        identifier = self.save_preset()
        before = deepcopy(self.window.task_presets)
        with patch.object(self.window, "save_user_config", return_value=False), patch(
                "app.winUI.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.assertFalse(self.window._save_task_preset("New"))
            self.assertEqual(self.window.task_presets, before)
            self.assertFalse(self.window._replace_task_preset(identifier, "Renamed"))
            self.assertEqual(self.window.task_presets, before)
            self.assertFalse(self.window._delete_task_preset(identifier))
        self.assertEqual(self.window.task_presets, before)
        self.assertEqual(self.panel.list.count(), 1)
        self.assertTrue(self.panel.status.property("error"))

    def test_apply_reports_failed_persistence_without_claiming_saved(self):
        identifier = self.save_preset()
        self.task_widget().checkbox.setChecked(False)
        with patch.object(self.window, "save_user_config", return_value=False):
            self.assertFalse(self.window._apply_task_preset(identifier))
        self.assertTrue(self.task_widget().is_checked())
        self.assertIn("적용했지만", self.panel.status.text())
        self.assertTrue(self.panel.status.property("error"))

    def test_missing_tasks_are_skipped_and_all_missing_preserves_current_list(self):
        identifier = self.save_preset()
        preset = self.window.task_presets[0]
        preset["tasks"].append({"name": "Removed", "entry": "Missing", "checked": True,
                                "selected_options": {}})
        self.assertTrue(self.window._apply_task_preset(identifier))
        self.assertEqual(self.window.option_list_widget.count(), 1)
        self.assertIn("없는 작업 1개", self.panel.status.text())
        preset["tasks"] = preset["tasks"][1:]
        current = self.task_widget()
        self.assertFalse(self.window._apply_task_preset(identifier))
        self.assertIs(self.task_widget(), current)

    def test_renamed_task_can_be_matched_by_entry(self):
        identifier = self.save_preset()
        self.window.task_presets[0]["tasks"][0]["name"] = "Previous_Name"
        self.assertTrue(self.window._apply_task_preset(identifier))
        self.assertEqual(self.task_widget().task_data["name"], "Test")

    def test_unconfigured_builtin_does_not_replace_list_when_resource_tasks_are_missing(self):
        identifier = self.save_preset()
        preset = self.window.task_presets[0]
        preset["tasks"][0].update(name="Removed", entry="Missing")
        preset["tasks"].insert(0, {"name": PROGRAM_LAUNCH_TASK_NAME, "entry": "__ProgramLaunch",
                                   "checked": False, "selected_options": {}})
        self.window.settings_panel.program_launch_checkbox.setChecked(True)
        before = self.window._serialize_task_list()
        self.assertFalse(self.window._apply_task_preset(identifier))
        self.assertEqual(self.window._serialize_task_list(), before)

    def test_program_launch_remains_controlled_by_global_setting(self):
        identifier = self.save_preset()
        preset = self.window.task_presets[0]
        preset["tasks"].insert(0, {"name": PROGRAM_LAUNCH_TASK_NAME, "entry": "__ProgramLaunch",
                                   "checked": True, "selected_options": {}})
        self.window.settings_panel.program_launch_checkbox.setChecked(False)
        self.assertTrue(self.window._apply_task_preset(identifier))
        self.assertEqual(self.window.option_list_widget.count(), 1)
        settings_patch = patch.object(self.window.settings_panel, "program_settings",
                                      return_value={"resolved_path": "C:/Example/program.exe"})
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.window.settings_panel.program_launch_checkbox.setChecked(True)
        self.assertTrue(self.window._apply_task_preset(identifier))
        self.assertEqual(self.task_widget().task_data["name"], PROGRAM_LAUNCH_TASK_NAME)
        self.assertTrue(self.task_widget().checkbox.isChecked())
        preset["tasks"] = preset["tasks"][1:]
        self.assertTrue(self.window._apply_task_preset(identifier))
        self.assertFalse(self.task_widget().checkbox.isChecked())
        self.assertTrue(self.window.settings_panel.program_launch_task_enabled())

    def test_reset_removes_saved_presets_and_pending_close_cannot_rewrite_them(self):
        self.save_preset()
        reset_settings_files(self.window.runtime.user_dir, deepcopy(self.window.settings_panel.config))
        self.assertEqual(self.read_config(), {})
        with patch.object(self.window, "_reset_settings_pending", True):
            self.assertFalse(self.window.save_user_config())
        self.assertEqual(self.read_config(), {})

    def test_config_directory_failure_is_reported_without_changing_library(self):
        with patch("app.winUI.Path.mkdir", side_effect=OSError("denied")), patch("builtins.print"):
            self.assertFalse(self.window._save_task_preset("Rejected"))
        self.assertEqual(self.window.task_presets, [])
        self.assertTrue(self.panel.status.property("error"))

    def test_runtime_lock_policy_is_preserved_without_mutating_active_queue(self):
        identifier = self.save_preset()
        window = self.window
        window.set_options_locked(True)
        self.assertFalse(self.panel.save_button.isEnabled())
        self.assertFalse(self.panel.apply_button.isEnabled())
        self.assertTrue(self.panel.list.isEnabled())
        self.assertFalse(window._apply_task_preset(identifier))
        window.set_runtime_option_editing_enabled(True)
        active_queue = window.build_execution_queue()
        expected = deepcopy(active_queue)
        self.task_widget().checkbox.setChecked(False)
        self.assertTrue(self.panel.apply_button.isEnabled())
        self.assertTrue(window._apply_task_preset(identifier))
        self.assertEqual(active_queue, expected)

    def test_startup_close_completion_and_reset_reject_edits(self):
        self.save_preset()
        for flag in ("_closing", "_close_pending", "_completion_pending", "_reset_settings_pending"):
            with self.subTest(flag=flag), patch.object(self.window, flag, True):
                self.window._apply_option_editing_policy()
                self.assertFalse(self.panel.save_button.isEnabled())
                self.assertFalse(self.window._save_task_preset("Rejected"))
        with patch.object(self.window.app_startup, "active", True):
            self.window._apply_option_editing_policy()
            self.assertFalse(self.window._apply_task_preset(self.panel.selected_id()))

    def test_tabs_keep_widths_and_avoid_horizontal_scroll_in_both_themes(self):
        window = self.window
        self.save_preset("긴 이름의 작업 프리셋 " * 5)
        window.show()
        for theme in ("light", "dark"):
            window.settings_panel.theme_combo.setCurrentIndex(window.settings_panel.theme_combo.findData(theme))
            for width in (1000, 1300):
                window.resize(width, 820)
                QApplication.processEvents()
                before = window.ui.workspaceSplitter.sizes()
                for index in (1, 0, 1, 0):
                    with self.subTest(theme=theme, width=width, index=index):
                        window._show_option_page(index)
                        QApplication.processEvents()
                        self.assertEqual(window.ui.workspaceSplitter.sizes(), before)
                        self.assertEqual(window.centralWidget().findChild(QWidget, "_1_settingStartWidget").width(), 312)
                        self.assertEqual(window.ui.scrollSettingWidget.horizontalScrollBar().maximum(), 0)
                        self.assertEqual(self.panel.list.horizontalScrollBar().maximum(), 0)
                        self.assertLessEqual(self.panel.save_button.geometry().right(), self.panel.width())
                        self.assertGreaterEqual(self.panel.name_input.width(), 80)
                        self.assertGreaterEqual(window.detail_tab_button.width(), window.detail_tab_button.sizeHint().width())
                        separator = window.findChild(QFrame, "detailTabSeparator")
                        self.assertEqual(separator.width(), 1)
                        self.assertEqual(separator.height(), 16)


if __name__ == "__main__":
    unittest.main()
