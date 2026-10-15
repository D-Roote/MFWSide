"""Missing interface has a safe Win32 UI; declared ADB never falls back to Win32."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from app.runtime import AppRuntime
from app.winUI import MainWindow


class InterfaceStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def runtime(self):
        with patch("pathlib.Path.open", side_effect=FileNotFoundError), \
                patch("app.runtime.create_user32", return_value=MagicMock()):
            return AppRuntime()

    def test_missing_interface_defaults_to_win32_but_cannot_load_resource(self):
        runtime = self.runtime()
        self.assertTrue(runtime.interface_missing)
        self.assertEqual(runtime.controller_config["type"], "Win32")
        self.assertFalse(runtime.interface["task"])
        self.assertFalse(runtime._load_resource()[0])

    def test_missing_interface_opens_settings_ui_and_disables_start(self):
        runtime = self.runtime()
        with tempfile.TemporaryDirectory() as directory:
            runtime.user_dir = Path(directory)
            with patch("app.winUI.AppRuntime", return_value=runtime), \
                    patch("app.settingsUI.find_auto_program_executable", return_value=None), \
                    patch("app.settingsUI.find_program_executable", return_value=None):
                window = MainWindow()
            try:
                self.assertEqual(window.settings_panel.controller_settings()["type"], "Win32")
                self.assertEqual(
                    window.settings_panel.program_path_row.description_label.text(),
                    "비워 두면 자동 검색 결과를 사용합니다. 폴더 또는 실행 파일을 지정할 수 있습니다.",
                )
                self.assertEqual(
                    window.settings_panel.program_path_input.placeholderText(),
                    "실행 파일 또는 실행 파일이 있는 폴더",
                )
                self.assertFalse(window.ui.workStartBtn.isEnabled())
                with patch.object(runtime, "initialize") as initialize:
                    window.on_task_start()
                initialize.assert_not_called()
                self.assertIn("interface.json", window.ui.logPrintText.toPlainText())
            finally:
                window.close()
                window.deleteLater()

    def test_adb_only_runtime_selects_declared_adb_preset(self):
        interface = {"controller": [{"name": "Adb", "type": "Adb", "adb": {}}],
                     "resource": [{"path": ["."]}], "task": []}
        with patch.object(AppRuntime, "_load_interface", return_value=interface), \
                patch("app.runtime.create_user32", return_value=MagicMock()):
            runtime = AppRuntime()
        self.assertFalse(runtime.interface_missing)
        self.assertEqual(runtime.controller_config["type"], "Adb")

    def test_non_object_interface_reports_clear_validation_error(self):
        with patch("pathlib.Path.open"), patch("app.runtime.json.load", return_value=[]):
            with self.assertRaisesRegex(ValueError, "최상위"):
                AppRuntime()


if __name__ == "__main__":
    unittest.main()
