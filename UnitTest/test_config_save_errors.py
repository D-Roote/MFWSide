import unittest
from unittest.mock import patch
import test_runtime_lifecycle as fixtures


class ConfigSaveErrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window

    def test_user_save_failure_is_visible_and_not_repeated_on_each_timer(self):
        with patch("app.winUI.write_json", side_effect=PermissionError("denied")), \
                patch("app.winUI.show_notice") as notice:
            self.assertFalse(self.window.save_user_config())
            self.assertFalse(self.window.save_user_config())
        notice.assert_called_once()
        self.assertIn("설정 파일 저장 실패", self.window.ui.logPrintText.toPlainText())
        self.assertTrue(self.window.save_user_config())
        self.assertEqual(self.window._config_save_error, "")

    def test_settings_save_failure_does_not_emit_saved_startup_change(self):
        panel = self.window.settings_panel
        changes = []
        panel.startup_changed.connect(lambda: changes.append(True))
        with patch.object(panel.store, "save", side_effect=PermissionError("denied")), \
                patch("app.settingsUI.show_notice") as notice:
            self.assertFalse(panel._save())
        notice.assert_called_once()
        self.assertEqual(changes, [])
