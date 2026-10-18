"""Registry operations are mocked; tests never register an actual login task."""
from pathlib import Path
import json
import tempfile
from unittest.mock import MagicMock, patch
import unittest

import test_runtime_lifecycle as fixtures
from app.pg_init import parse_extra_arguments
from app.startupSettings import DEFAULT_STARTUP_CONFIG, WindowsStartupRegistration, normalize_startup_config


class StartupSettingsBackendTests(unittest.TestCase):
    def test_normalization_defaults_and_ranges(self):
        self.assertEqual(normalize_startup_config(None), DEFAULT_STARTUP_CONFIG)
        config = normalize_startup_config({"auto_start_tasks": "yes", "launch_program": True,
                                           "emulator_wait_seconds": -1, "unknown": True})
        self.assertFalse(config["auto_start_tasks"])
        self.assertTrue(config["launch_program"])
        self.assertEqual(config["emulator_wait_seconds"], 60)
        self.assertNotIn("unknown", config)
        self.assertEqual(normalize_startup_config({"emulator_wait_seconds": 0})["emulator_wait_seconds"], 0)

    def test_windows_extra_arguments_preserve_quotes_and_never_use_shell(self):
        self.assertEqual(parse_extra_arguments('--profile "User One" -v 0'), ["--profile", "User One", "-v", "0"])
        self.assertEqual(parse_extra_arguments(""), [])
        for value in ('"unclosed', "line\nnext", "\0"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_extra_arguments(value)

    def test_task_wait_defaults_to_immediate_and_rejects_invalid_values(self):
        self.assertEqual(normalize_startup_config({})["task_wait_seconds"], 0)
        for value in (0, 15, 3600):
            with self.subTest(value=value):
                self.assertEqual(normalize_startup_config({"task_wait_seconds": value})["task_wait_seconds"], value)
        for value in (-1, 3601, True, 1.5, "15", None):
            with self.subTest(invalid=value):
                self.assertEqual(normalize_startup_config({"task_wait_seconds": value})["task_wait_seconds"], 0)

    def test_source_command_uses_pythonw_and_absolute_main(self):
        with tempfile.TemporaryDirectory(prefix="startup test ") as directory:
            root = Path(directory)
            (root / "main.py").touch()
            (root / "pythonw.exe").touch()
            with patch("app.startupSettings.sys.executable", str(root / "python.exe")), \
                    patch("app.startupSettings.sys.frozen", False, create=True):
                command = WindowsStartupRegistration(root).command()
            self.assertIn(str(root / "pythonw.exe"), command)
            self.assertIn(str(root / "main.py"), command)
            self.assertNotIn("powershell", command)

    def test_frozen_command_does_not_use_python_or_main_py(self):
        with patch("app.startupSettings.sys.executable", "C:/App/MAABA.exe"), \
                patch("app.startupSettings.sys.frozen", True, create=True):
            command = WindowsStartupRegistration("C:/App").command()
        self.assertTrue(command.endswith("MAABA.exe"))
        self.assertNotIn("main.py", command)

    def test_registry_has_per_installation_key_and_only_current_user_value(self):
        first = WindowsStartupRegistration("C:/First")
        second = WindowsStartupRegistration("C:/Second")
        self.assertNotEqual(first.value_name, second.value_name)
        registry = MagicMock()
        with patch.dict("sys.modules", {"winreg": registry}), patch.object(first, "command", return_value="expected"):
            first.set_enabled(True)
            key = registry.CreateKeyEx.return_value.__enter__.return_value
            registry.SetValueEx.assert_called_once_with(key, first.value_name, 0, registry.REG_SZ, "expected")
            first.set_enabled(False)
            key = registry.OpenKey.return_value.__enter__.return_value
            registry.DeleteValue.assert_called_once_with(key, first.value_name)
            registry.DeleteKey.assert_not_called()

    def test_read_does_not_modify_registry_and_missing_entry_is_disabled(self):
        registration = WindowsStartupRegistration("C:/Example")
        registry = MagicMock()
        registry.OpenKey.side_effect = FileNotFoundError
        with patch.dict("sys.modules", {"winreg": registry}):
            self.assertFalse(registration.is_enabled())
        registry.SetValueEx.assert_not_called()
        registry.DeleteValue.assert_not_called()


class StartupSettingsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        for method, value in (("is_enabled", False), ("set_enabled", None)):
            patcher = patch(f"app.startupSettings.WindowsStartupRegistration.{method}", return_value=value)
            mock = patcher.start()
            setattr(self, method, mock)
            self.addCleanup(patcher.stop)
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.panel = self.window.settings_panel

    def test_startup_labels_and_warning_exclude_update_option(self):
        self.assertEqual(self.panel.navigation.item(1).text(), "시작 설정")
        self.assertEqual(len(self.panel.startup_checkboxes), 4)
        self.assertIn("일부 프로그램", self.panel.startup_warning.text())
        labels = [label.text() for label in self.panel._sections[1].findChildren(fixtures.QLabel)]
        self.assertFalse(any("업데이트" in label for label in labels))
        self.set_enabled.assert_not_called()

    def test_startup_preferences_are_persisted(self):
        self.panel.startup_checkboxes["auto_start_tasks"].setChecked(True)
        self.panel.auto_start_wait_spin.setValue(12)
        self.panel.startup_wait_spin.setValue(45)
        self.panel.restart_connection_checkbox.setChecked(True)
        saved = json.loads(self.panel.store.config_path.read_text(encoding="utf-8"))
        self.assertTrue(saved["startup"]["auto_start_tasks"])
        self.assertTrue(saved["startup"]["restart_on_connection_failure"])
        self.assertEqual(saved["startup"]["emulator_wait_seconds"], 45)
        self.assertEqual(saved["startup"]["task_wait_seconds"], 12)

    def test_auto_task_wait_row_is_always_visible_and_restores_saved_value(self):
        self.assertFalse(self.panel.auto_start_wait_row.isHidden())
        self.panel.startup_checkboxes["auto_start_tasks"].setChecked(True)
        self.assertFalse(self.panel.auto_start_wait_row.isHidden())
        self.panel.auto_start_wait_spin.setValue(15)
        self.panel.startup_checkboxes["auto_start_tasks"].setChecked(False)
        self.assertFalse(self.panel.auto_start_wait_row.isHidden())
        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            restored = fixtures.MainWindow()
        self.addCleanup(restored.deleteLater)
        panel = restored.settings_panel
        self.assertFalse(panel.startup_checkboxes["auto_start_tasks"].isChecked())
        self.assertFalse(panel.auto_start_wait_row.isHidden())
        self.assertEqual(panel.auto_start_wait_spin.value(), 15)
        layout = self.panel._section_widgets["program"].layout()
        auto_row = self.panel.startup_checkboxes["auto_start_tasks"].parentWidget()
        self.assertEqual(layout.indexOf(self.panel.auto_start_wait_row), layout.indexOf(auto_row) + 1)

    def test_static_startup_guidance_is_in_section_description_not_status(self):
        description = self.panel._section_widgets["program"].description_label.text()
        self.assertIn("\n변경한 시작 동작은 다음 MAABA 실행부터 적용됩니다.", description)
        self.panel.startup_checkboxes["auto_start_tasks"].setChecked(True)
        self.assertTrue(self.panel.startup_registration_status.isHidden())
        self.assertFalse(hasattr(self.panel, "startup_status"))
        self.assertFalse(hasattr(self.panel, "startup_cancel_button"))

    def test_program_paths_are_one_group_with_manual_description_bottom_inset(self):
        layout = self.panel._section_widgets["program"].layout()
        launch_row = self.panel.startup_checkboxes["launch_program"].parentWidget()
        self.assertLess(layout.indexOf(launch_row), layout.indexOf(self.panel.program_auto_status))
        self.assertFalse(hasattr(self.panel, "program_paths_divider"))
        for row in (self.panel.program_path_row, self.panel.program_extra_row):
            self.assertTrue(row.property("joinedProgramRow"))
        self.assertLess(layout.indexOf(self.panel.startup_warning_divider), layout.indexOf(self.panel.startup_warning))
        self.assertEqual(self.panel.program_path_row.layout().contentsMargins().bottom(), 12)
        self.assertEqual(self.panel.program_active_status_container.layout().contentsMargins().bottom(), 12)
        divider = self.panel.startup_warning_divider
        self.assertEqual(divider.height(), 1)
        self.assertEqual(divider.objectName(), "settingsDivider")

    def test_registry_failure_rolls_checkbox_back(self):
        self.set_enabled.side_effect = PermissionError("denied")
        self.panel.startup_checkboxes["launch_on_login"].setChecked(True)
        self.assertFalse(self.panel.startup_checkboxes["launch_on_login"].isChecked())
        self.assertIn("실패", self.panel.startup_registration_status.text())
        self.set_enabled.side_effect = None
        self.panel.startup_checkboxes["launch_on_login"].setChecked(True)
        self.assertTrue(self.panel.startup_registration_status.isHidden())

    def test_startup_spacing_and_dividers_fit_both_themes_at_minimum_width(self):
        from PySide6.QtCore import QPoint
        self.panel.startup_checkboxes["auto_start_tasks"].setChecked(True)
        self.panel.controller_combo.addItem("Adb", {"name": "Adb", "type": "Adb"})
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        for theme in ("light", "dark"):
            self.panel.theme_combo.setCurrentIndex(self.panel.theme_combo.findData(theme))
            for controller in (0, self.panel.controller_combo.count() - 1):
                with self.subTest(theme=theme, controller=controller):
                    self.panel.controller_combo.setCurrentIndex(controller)
                    self.window.resize(self.window.minimumWidth(), 820)
                    fixtures.QTest.qWait(20)
                    contents = self.panel.detail_contents
                    label = self.panel.program_path_row.description_label
                    description_bottom = label.mapTo(contents, QPoint(0, label.height())).y()
                    next_border = self.panel.program_extra_row.mapTo(contents, QPoint()).y()
                    self.assertGreaterEqual(next_border - description_bottom, 12)
                    self.assertLessEqual(label.heightForWidth(label.width()), 2 * label.fontMetrics().lineSpacing())
                    self.assertEqual(self.panel.detail_scroll.horizontalScrollBar().maximum(), 0)
                    divider = self.panel.startup_warning_divider
                    color = divider.grab().toImage().pixelColor(0, 0).name()
                    self.assertEqual(color, "#263650" if theme == "dark" else "#eef2f7")
                    warning_gap = self.panel.startup_warning.y() - divider.geometry().bottom() - 1
                    self.assertGreaterEqual(warning_gap, 12)

    def test_extra_arguments_are_combined_with_manual_target_and_validated(self):
        target = self.window.runtime.user_dir / "player.exe"
        target.touch()
        self.panel.program_path_input.setText(f'"{target}" -v 0')
        self.panel._apply_program_path()
        self.panel.program_extra_input.setText('--profile "User One"')
        settings = self.panel.program_settings()
        self.assertEqual(settings["launch_arguments"], ["-v", "0", "--profile", "User One"])
        self.panel.program_extra_input.setText('"unclosed')
        self.assertEqual(self.panel.program_settings()["resolved_path"], "")
        self.assertIn("추가 인수", self.panel.program_active_status.text())

    def test_url_arguments_are_rejected_before_launch(self):
        target = self.window.runtime.user_dir / "Game.url"
        target.touch()
        self.panel.program_path_input.setText(f'"{target}"')
        self.panel._apply_program_path()
        self.panel.program_extra_input.setText("-v 0")
        self.assertIn(".url", self.panel.program_settings()["arguments_error"])
        self.assertFalse(self.panel.program_settings()["resolved_path"])

    def test_adb_only_wait_and_restart_controls(self):
        self.assertTrue(self.panel.startup_wait_row.isHidden())
        self.assertTrue(self.panel.restart_row.isHidden())
        self.panel.controller_combo.addItem("Adb", {"name": "Adb", "type": "Adb"})
        self.panel.controller_combo.setCurrentIndex(self.panel.controller_combo.count() - 1)
        self.assertFalse(self.panel.startup_wait_row.isHidden())
        self.assertFalse(self.panel.restart_row.isHidden())
        self.panel.startup_wait_spin.setValue(30)
        self.panel.restart_connection_checkbox.setChecked(True)
        settings = self.panel.program_settings()
        self.assertEqual(settings["adb_startup_wait_seconds"], 30)
        self.assertTrue(settings["restart_on_connection_failure"])

    def test_wait_control_ignores_wheel(self):
        for spin in (self.panel.startup_wait_spin, self.panel.auto_start_wait_spin):
            with self.subTest(control=spin.accessibleName()):
                event = MagicMock()
                previous = spin.value()
                spin.wheelEvent(event)
                event.ignore.assert_called_once()
                self.assertEqual(spin.value(), previous)


if __name__ == "__main__":
    unittest.main()
