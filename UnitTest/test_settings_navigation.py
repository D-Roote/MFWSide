"""Internal settings moves, scroll linkage and stable persisted IDs."""
import json
from unittest.mock import MagicMock, patch
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import QModelIndex, QPoint, Qt
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

    def test_top_insert_line_has_room_after_moving_to_first(self):
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        fixtures.QApplication.processEvents()
        navigation = self.panel.navigation
        navigation.model().moveRow(QModelIndex(), 3, QModelIndex(), 0)
        fixtures.QApplication.processEvents()
        top = navigation.visualItemRect(navigation.item(0)).top()
        # A centered 2px stroke must have space on both sides of the top edge.
        self.assertGreaterEqual(top, 2)
        self.assertGreaterEqual(navigation.spacing(), 2)
        self.assertLess(top, 10)

    def test_selected_card_keeps_its_top_border_inside_viewport(self):
        self.window.resize(self.window.minimumWidth(), 700)
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        fixtures.QApplication.processEvents()
        for row in (0, 1):
            with self.subTest(row=row):
                self.panel.navigation.setCurrentRow(row)
                fixtures.QApplication.processEvents()
                section = self.panel._sections[row]
                top = section.mapTo(self.panel.detail_scroll.viewport(), QPoint()).y()
                self.assertEqual(top, self.panel.SECTION_TOP_INSET)
                self.assertEqual(self.panel.navigation.currentRow(), row)


if __name__ == "__main__":
    unittest.main()
