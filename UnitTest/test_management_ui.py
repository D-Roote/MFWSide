import json
from pathlib import Path
from unittest.mock import patch
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox


class ManagementUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        registration = patch.object(self.window.settings_panel.startup_registration, "is_enabled", return_value=False)
        registration.start()
        self.addCleanup(registration.stop)
        unregister = patch.object(self.window.settings_panel.startup_registration, "set_enabled")
        unregister.start()
        self.addCleanup(unregister.stop)
        self.panel = self.window.settings_panel.management
        self.user = self.window.runtime.user_dir
        self.panel.configure(self.user, self.user / "sandbox", self.user / "sandbox/app",
                             self.user / "sandbox/assets/resources", self.window._can_manage_data)
        self.addCleanup(self.stop)

    def stop(self):
        self.panel.shutdown()
        if self.panel.worker:
            self.panel.worker.wait(5000)
        fixtures.QApplication.processEvents()

    def wait_worker(self):
        for _ in range(200):
            if not self.panel.busy:
                return
            fixtures.QTest.qWait(10)
        self.fail("management worker did not finish")

    def test_management_default_position_and_async_inventory(self):
        self.assertEqual(self.window.settings_panel.section_order()[-2:], ["appearance", "management"])
        directory = self.user / "debug"
        directory.mkdir()
        (directory / "old.log").write_text("example")
        self.panel.scan()
        self.assertFalse(self.window.ui.workStartBtn.isEnabled())
        self.wait_worker()
        self.assertGreater(self.panel.tree.topLevelItemCount(), 0)
        self.assertIn("사용 용량", self.panel.status.text())
        self.assertTrue(self.window.ui.workStartBtn.isEnabled())

    def test_delete_requires_confirmation_and_does_not_reset_settings(self):
        directory = self.user / "debug"
        directory.mkdir()
        log = directory / "old.log"
        log.write_text("example")
        self.panel.scan()
        self.wait_worker()
        for index, usage in enumerate(self.panel.usages):
            if usage.group.key == "debug":
                self.panel.tree.topLevelItem(index).setCheckState(0, Qt.CheckState.Checked)
        with patch("app.managementUI.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.panel.delete_selected()
        self.assertTrue(log.exists())
        with patch("app.managementUI.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.panel.delete_selected()
        self.wait_worker()
        self.assertFalse(log.exists())
        self.assertTrue((self.user / "config/user_config.json").is_file())
        self.assertIn("삭제 완료", self.panel.status.text())

    def test_reset_cancel_does_not_change_config(self):
        path = self.user / "config/user_config.json"
        before = path.read_bytes()
        with patch("app.winUI.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.window._reset_all_settings()
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(self.window._reset_settings_pending)

    def test_reset_closes_without_overwriting_new_defaults(self):
        self.window.settings_panel.runtime_edit_checkbox.setChecked(True)
        with patch("app.winUI.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.window._reset_all_settings()
        self.assertTrue(self.window._reset_settings_pending)
        self.window.save_user_config()
        self.assertEqual(json.loads((self.user / "config/user_config.json").read_text()), {})
        config = json.loads((self.user / "config/maa_config.json").read_text())
        self.assertFalse(config["general"]["allow_option_edits_while_running"])

    def test_reset_during_run_is_blocked_before_confirmation(self):
        self.window.isRunning = True
        with patch("app.winUI.QMessageBox.question") as question:
            self.window._reset_all_settings()
        question.assert_not_called()
        self.assertFalse(self.window._reset_settings_pending)
        self.window.isRunning = False


if __name__ == "__main__":
    unittest.main()
