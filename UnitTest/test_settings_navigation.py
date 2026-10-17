"""Internal settings moves, scroll linkage and stable persisted IDs."""
import json
from unittest.mock import MagicMock, patch
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import QModelIndex, Qt
from app.settingsNavigation import SETTINGS_SECTIONS, normalize_section_order


class SettingsNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.panel = self.window.settings_panel

    def test_invalid_and_duplicate_ids_are_normalized(self):
        self.assertEqual(normalize_section_order(None), [key for key, _ in SETTINGS_SECTIONS])
        result = normalize_section_order(["appearance", "appearance", [], False, "unknown", "general"])
        self.assertEqual(len(result), len(SETTINGS_SECTIONS))
        self.assertEqual(result.count("appearance"), 1)
        self.assertLess(result.index("appearance"), result.index("general"))

    def test_move_updates_card_order_and_saved_config(self):
        self.panel.navigation.setCurrentRow(3)
        self.assertTrue(self.panel.navigation.model().moveRow(QModelIndex(), 3, QModelIndex(), 0))
        self.assertEqual(self.panel.section_order()[0], "appearance")
        self.assertEqual(self.panel._sections[0].property("settingsSectionKey"), "appearance")
        self.assertEqual(self.panel.detail_layout.itemAt(0).widget(), self.panel._sections[0])
        saved = json.loads(self.panel.store.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["section_order"], self.panel.section_order())

    def test_reopen_restores_order_but_keeps_controls(self):
        self.panel.navigation.model().moveRow(QModelIndex(), 0, QModelIndex(), self.panel.navigation.count())
        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            restored = fixtures.MainWindow()
        self.addCleanup(restored.deleteLater)
        self.assertEqual(restored.settings_panel.section_order(), self.panel.section_order())
        self.assertEqual(restored.settings_panel._sections[-1].property("settingsSectionKey"), "general")
        self.assertFalse(restored.settings_panel.minimize_enabled())

    def test_keyboard_move_and_external_drop_restriction(self):
        self.panel.navigation.setCurrentRow(1)
        fixtures.QTest.keyClick(self.panel.navigation, Qt.Key.Key_Up, Qt.KeyboardModifier.AltModifier)
        self.assertEqual(self.panel.section_order()[0], "program")
        event = MagicMock()
        event.source.return_value = self.window.ui.taskOptionList
        self.panel.navigation.dropEvent(event)
        event.ignore.assert_called_once()


if __name__ == "__main__":
    unittest.main()
