"""Windows shortcut Target parsing and manual-only ADB program preferences."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_runtime_lifecycle as fixtures
from app.pg_init import parse_manual_program_command
from app.settingsUI import ADB_MANUAL_PATH_DESCRIPTION
from PySide6.QtTest import QTest


class ShortcutTargetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.executable = Path(self.directory.name) / "App Player.exe"
        self.executable.touch()

    def test_quoted_executable_and_instance_arguments(self):
        executable, arguments = parse_manual_program_command(f'"{self.executable}" -v 0')
        self.assertEqual(executable, self.executable.resolve())
        self.assertEqual(arguments, ["-v", "0"])

    def test_windows_roundtrip_with_spaces_backslashes_quotes_and_shell_literals(self):
        arguments = ["-v", "2", "--path", "C:\\folder with spaces\\", 'a"b', "&", "echo", "|", "$HOME"]
        command = subprocess.list2cmdline([str(self.executable), *arguments])
        self.assertEqual(parse_manual_program_command(command)[1], arguments)

    def test_invalid_empty_multiline_missing_exe_and_unclosed_quotes_are_rejected(self):
        for command in ("", '"not-found.exe" -v 0', f'"{self.executable} -v 0',
                        str(self.executable), f'"{self.executable}"\n-v 0'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                parse_manual_program_command(command)

    def test_directory_or_non_exe_is_not_a_launch_target(self):
        script = Path(self.directory.name) / "script.cmd"
        script.touch()
        for path in (Path(self.directory.name), script):
            with self.assertRaises(ValueError):
                parse_manual_program_command(f'"{path}"')


class AdbProgramSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window, self.panel = self.fixture.window, self.fixture.window.settings_panel
        self.preset = {"name": "Adb", "type": "Adb", "label": "ADB"}
        self.panel.controllers.append(self.preset)
        self.panel.controller_combo.addItem("ADB", self.preset)
        self.executable = self.window.runtime.user_dir / "App Player.exe"
        self.executable.touch()

    def select_adb(self):
        self.panel.controller_combo.setCurrentIndex(self.panel.controller_combo.count() - 1)

    def test_switch_to_adb_never_searches_auto_path_and_rejects_empty_manual(self):
        with patch("app.settingsUI.find_auto_program_executable") as auto, \
                patch("app.settingsUI.find_program_executable") as resolve:
            self.select_adb()
            config = self.panel.program_settings()
            self.panel._apply_program_path()
        auto.assert_not_called()
        resolve.assert_not_called()
        self.assertEqual(config["resolved_path"], "")
        self.assertTrue(config["manual_only"])
        self.assertEqual(self.panel.program_auto_status.text(), "자동 경로를 사용할 수 없습니다.")
        self.assertFalse(self.panel.program_auto_status.isEnabled())
        self.assertEqual(self.panel.program_path_row.description_label.text(), ADB_MANUAL_PATH_DESCRIPTION)
        self.assertEqual(ADB_MANUAL_PATH_DESCRIPTION,
                         '실행 디바이스의 바로가기 생성 후, 우클릭-속성-바로 가기 탭 의 "대상"을 복사하여 사용합니다.')
        self.assertFalse(self.panel.program_active_status.property("pathValid"))
        self.assertNotIn("사용 경로:", self.panel.program_active_status.text())

    def test_confirmed_target_preserves_arguments_and_survives_reload(self):
        self.select_adb()
        command = f'"{self.executable}" -v 0'
        self.panel.program_path_input.setText(command)
        self.panel._apply_program_path()
        config = self.panel.program_settings()
        self.assertEqual(config["resolved_path"], str(self.executable.resolve()))
        self.assertEqual(config["launch_arguments"], ["-v", "0"])
        self.assertTrue(self.panel.program_active_status.property("pathValid"))
        saved = json.loads(self.panel.store.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["program"]["manual_path"], command)
        self.panel.config = saved
        self.panel._confirmed_manual_path = ""
        self.panel._apply_config()
        self.assertEqual(self.panel.program_settings()["launch_arguments"], ["-v", "0"])
        self.panel.program_apply_button.click()
        self.assertFalse(self.panel.program_settings()["resolved_path"])

    def test_deleted_manual_executable_does_not_fall_back_to_detected_win32_game(self):
        self.select_adb()
        self.panel.program_path_input.setText(f'"{self.executable}" -v 1')
        self.panel._apply_program_path()
        self.executable.unlink()
        with patch("app.settingsUI.find_auto_program_executable", return_value=Path("other.exe")) as auto:
            self.assertFalse(self.panel.program_settings()["resolved_path"])
            self.panel._refresh_program_status()
        auto.assert_not_called()

    def test_saved_adb_is_applied_before_any_auto_search_at_startup(self):
        self.window.runtime.interface["controller"] = [self.preset]
        self.window.runtime.resource_config["controller"] = ["Adb"]
        with patch("app.winUI.AppRuntime", return_value=self.window.runtime), \
                patch("app.settingsUI.find_auto_program_executable") as auto:
            restarted = fixtures.MainWindow()
        self.addCleanup(restarted.deleteLater)
        self.assertEqual(restarted.settings_panel.controller_settings()["type"], "Adb")
        auto.assert_not_called()

    def test_win32_switch_restores_automatic_search_and_description(self):
        self.select_adb()
        with patch("app.settingsUI.find_auto_program_executable", return_value=None) as auto:
            self.panel.controller_combo.setCurrentIndex(0)
        self.assertGreater(auto.call_count, 0)
        self.assertTrue(self.panel.program_auto_status.isEnabled())
        self.assertNotIn("바로가기", self.panel.program_path_row.description_label.text())

    def test_full_shortcut_description_fits_after_resize_in_both_themes(self):
        self.select_adb()
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        for theme in ("light", "dark"):
            self.panel.theme_combo.setCurrentIndex(self.panel.theme_combo.findData(theme))
            for width in (1000, 1100, 1400):
                self.window.resize(width, 850)
                QTest.qWait(20)
                label = self.panel.program_path_row.description_label
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                self.assertLessEqual(self.panel.detail_contents.width(), self.panel.detail_scroll.viewport().width())


if __name__ == "__main__":
    unittest.main()
