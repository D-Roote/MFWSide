"""Internal settings moves, scroll linkage and stable persisted IDs."""
import json
from unittest.mock import MagicMock, patch
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import QEvent, QModelIndex, QPoint, Qt
from PySide6.QtWidgets import QLineEdit
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

    def show_settings(self):
        self.window.resize(self.window.minimumWidth(), 1000)
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        fixtures.QApplication.processEvents()

    def test_clicking_partially_visible_section_highlights_it_without_scroll_jump(self):
        self.show_settings()
        checkbox = self.panel.startup_checkboxes["minimize_app"]
        bar = self.panel.detail_scroll.verticalScrollBar()
        section = self.panel._section_widgets["program"]
        bar.setValue(section.y() - 100)
        self.assertEqual(self.panel.navigation.currentRow(), 0)
        before = bar.value()
        fixtures.QTest.mouseClick(checkbox, Qt.MouseButton.LeftButton)
        self.assertTrue(checkbox.isChecked())
        self.assertEqual(self.panel.navigation.currentRow(), self.panel.section_order().index("program"))
        self.assertEqual(bar.value(), before)
        # Explicit subsequent scrolling still tracks the topmost card.
        bar.setValue(0)
        self.assertEqual(self.panel.navigation.currentRow(), 0)

    def test_keyboard_editing_tracks_the_section_after_reorder(self):
        self.show_settings()
        self.panel.navigation.model().moveRow(QModelIndex(), 3, QModelIndex(), 0)
        combo = self.panel.theme_combo
        self.panel.detail_scroll.verticalScrollBar().setValue(0)
        combo.setFocus()
        fixtures.QApplication.processEvents()
        fixtures.QTest.keyClick(combo, Qt.Key.Key_Down)
        self.assertEqual(self.panel.section_order()[self.panel.navigation.currentRow()], "appearance")

    def test_labels_and_late_added_connection_controls_track_their_section(self):
        self.show_settings()
        panel = self.panel
        label = panel.program_path_row.description_label
        fixtures.QApplication.sendEvent(label, QEvent(QEvent.Type.FocusIn))
        self.assertEqual(panel.section_order()[panel.navigation.currentRow()], "program")
        # The real connection panel is attached after SettingsPanel construction.
        editor = panel._connection_group.findChild(QLineEdit)
        self.assertIsNotNone(editor)
        fixtures.QApplication.sendEvent(editor, QEvent(QEvent.Type.FocusIn))
        self.assertEqual(panel.section_order()[panel.navigation.currentRow()], "controller")

    def test_management_buttons_highlight_management_without_forced_scroll(self):
        self.show_settings()
        bar = self.panel.detail_scroll.verticalScrollBar()
        section = self.panel._section_widgets["management"]
        bar.setValue(max(0, section.y() - 100))
        before = bar.value()
        fixtures.QApplication.sendEvent(self.panel.management.scan_button, QEvent(QEvent.Type.FocusIn))
        self.assertEqual(self.panel.section_order()[self.panel.navigation.currentRow()], "management")
        self.assertEqual(bar.value(), before)


if __name__ == "__main__":
    unittest.main()
