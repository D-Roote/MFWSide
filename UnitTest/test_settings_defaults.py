"""First-install defaults do not replace an existing user's preferences."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_runtime_lifecycle as fixtures
from PySide6.QtWidgets import QCheckBox
from app.settingsUI import DEFAULT_MAA_CONFIG, SettingsStore
from app.storageManagement import reset_settings_files


ENABLED = {"allow_option_edits_while_running", "clear_log_on_start", "auto_cleanup_on_exit"}


class SettingsDefaultStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.user = Path(temporary.name)
        self.path = self.user / "config/maa_config.json"
        self.store = SettingsStore(self.path)

    def test_first_install_enables_exactly_the_three_requested_options(self):
        actual = self.store.load()
        self.assertEqual({key for key, value in actual["general"].items() if value is True}, ENABLED)
        self.assertTrue(all(value is False for value in actual["startup"].values() if isinstance(value, bool)))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), actual)

    def test_existing_explicit_values_are_preserved_even_when_opposite_to_defaults(self):
        saved = {key: not value for key, value in DEFAULT_MAA_CONFIG["general"].items()}
        SettingsStore._write_json(self.path, {"general": saved})
        actual = self.store.load()
        self.assertEqual(actual["general"], saved)
        self.assertEqual(self.store.load()["general"], saved)

    def test_missing_and_invalid_values_use_new_defaults_without_touching_saved_choices(self):
        SettingsStore._write_json(self.path, {"general": {"clear_log_on_start": False,
                                                        "allow_option_edits_while_running": "false",
                                                        "program_launch_task_enabled": "true"}})
        actual = self.store.load()["general"]
        self.assertTrue(actual["allow_option_edits_while_running"])
        self.assertFalse(actual["program_launch_task_enabled"])
        self.assertFalse(actual["clear_log_on_start"])
        self.assertTrue(actual["auto_cleanup_on_exit"])

    def test_reset_uses_the_same_three_enabled_defaults(self):
        SettingsStore._write_json(self.path, {"general": {key: not value for key, value in DEFAULT_MAA_CONFIG["general"].items()}})
        reset_settings_files(self.user, deepcopy(DEFAULT_MAA_CONFIG))
        actual = self.store.load()
        self.assertEqual({key for key, value in actual["general"].items() if value is True}, ENABLED)
        self.assertTrue(all(not value for value in actual["startup"].values() if isinstance(value, bool)))

    def test_legacy_explicit_program_launch_choice_is_still_migrated(self):
        for removed in (True, False):
            with self.subTest(removed=removed):
                SettingsStore._write_json(self.path, {"general": {"remove_program_launch_task": removed}})
                self.assertIs(self.store.load()["general"]["program_launch_task_enabled"], not removed)


class FirstInstallSettingsUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.addCleanup(self.fixture.doCleanups)
        with patch("app.settingsUI.WindowsStartupRegistration.is_enabled", return_value=False):
            self.fixture.setUp(existing_settings=False)
        self.window = self.fixture.window
        self.panel = self.window.settings_panel

    def test_only_requested_settings_checkboxes_are_checked(self):
        actual = {checkbox for checkbox in self.panel.findChildren(QCheckBox) if checkbox.isChecked()}
        self.assertEqual(actual, {self.panel.runtime_edit_checkbox, self.panel.clear_log_checkbox, self.panel.auto_cleanup_checkbox})
        self.assertTrue(self.window._allow_option_edits_while_running)
        self.assertFalse(self.panel.program_launch_task_enabled())

    def test_first_install_keeps_resource_task_defaults_without_builtin_launch_task(self):
        self.assertEqual(self.window.option_list_widget.count(), 1)
        task = self.window.option_list_widget.itemWidget(self.window.option_list_widget.item(0))
        self.assertEqual(task.task_data["name"], "Test")
        self.assertTrue(task.checkbox.isChecked())


if __name__ == "__main__":
    unittest.main()
