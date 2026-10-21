"""Focus rendering and keyboard navigation without devices or external apps."""
from unittest.mock import patch
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle
from app.controlStyle import KeyboardControlStyle, install_control_style


class KeyboardFocusStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.window.show()
        fixtures.QTest.qWait(10)

    def test_style_is_installed_once_and_owns_a_separate_base(self):
        style = QApplication.instance()._mfw_control_style
        self.assertIsInstance(style, KeyboardControlStyle)
        self.assertIs(install_control_style(), style)
        self.assertIs(install_control_style(), style)
        self.assertIs(style.parent(), QApplication.instance())
        self.assertIsNot(style.baseStyle(), style)

    def test_only_native_focus_primitive_is_suppressed(self):
        style = install_control_style()
        with patch.object(QProxyStyle, "drawPrimitive") as draw:
            style.drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect, None, None)
            draw.assert_not_called()
            style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, None, None)
            draw.assert_called_once_with(QStyle.PrimitiveElement.PE_IndicatorCheckBox, None, None, None)

    def focus_image(self, widget):
        self.window.ui.workStartBtn.setFocus()
        fixtures.QTest.qWait(5)
        unfocused = widget.grab().toImage()
        widget.setFocus(Qt.FocusReason.TabFocusReason)
        fixtures.QTest.qWait(5)
        self.assertTrue(widget.hasFocus())
        return unfocused, widget.grab().toImage()

    def test_top_tabs_have_no_inner_keyboard_frame_in_both_themes(self):
        for theme in ("light", "dark"):
            combo = self.window.settings_panel.theme_combo
            combo.setCurrentIndex(combo.findData(theme))
            for button, page in ((self.window.ui.dashboardNavButton, self.window.ui.mainTab),
                                 (self.window.ui.extraFeaturesNavButton, self.window.ui.extraFeaturesTab),
                                 (self.window.ui.settingsNavButton, self.window.ui.settingTab)):
                with self.subTest(theme=theme, button=button.objectName()):
                    self.window.ui.mainPages.setCurrentWidget(page)
                    fixtures.QTest.qWait(10)
                    button.clearFocus()
                    before, after = self.focus_image(button)
                    self.assertEqual(before, after)
                    self.assertNotEqual(button.focusPolicy(), Qt.FocusPolicy.NoFocus)

    def test_left_right_navigation_and_space_activation_remain_available(self):
        first = self.window.ui.dashboardNavButton
        first.setFocus(Qt.FocusReason.TabFocusReason)
        fixtures.QTest.keyClick(first, Qt.Key.Key_Right)
        self.assertIs(QApplication.focusWidget(), self.window.ui.extraFeaturesNavButton)
        fixtures.QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Right)
        self.assertIs(QApplication.focusWidget(), self.window.ui.settingsNavButton)
        fixtures.QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Space)
        self.assertIs(self.window.ui.mainPages.currentWidget(), self.window.ui.settingTab)
        fixtures.QTest.keyClick(QApplication.focusWidget(), Qt.Key.Key_Left)
        self.assertIs(QApplication.focusWidget(), self.window.ui.extraFeaturesNavButton)

    def test_other_keyboard_buttons_render_the_same_for_mouse_and_tab_focus(self):
        self.window.log_action_menu_controller.set_expanded(True)
        controls = (self.window.detail_tab_button, self.window.preset_tab_button,
                    self.window.ui.taskResetButton, self.window.ui.taskAddButton,
                    self.window.ui.endSettingBtn, self.window.ui.logCopyButton,
                    self.window.ui.logClearButton, self.window.ui.logSaveButton,
                    self.window.ui.logMenuToggleButton)
        for theme in ("light", "dark"):
            combo = self.window.settings_panel.theme_combo
            combo.setCurrentIndex(combo.findData(theme))
            for control in controls:
                if control.focusPolicy() == Qt.FocusPolicy.NoFocus:
                    continue  # Preserve controls intentionally excluded from the Tab chain.
                with self.subTest(theme=theme, control=control.objectName()):
                    images = []
                    for reason in (Qt.FocusReason.MouseFocusReason, Qt.FocusReason.TabFocusReason):
                        self.window.ui.dashboardNavButton.setFocus()
                        control.setFocus(reason)
                        fixtures.QTest.qWait(10)
                        self.assertTrue(control.hasFocus())
                        images.append(control.grab().toImage())
                    self.assertEqual(*images)

    def test_combo_popup_and_log_context_menu_keep_keyboard_selection(self):
        combo = self.window.monitor.panels[0].preset_combo
        combo.setFocus(Qt.FocusReason.TabFocusReason)
        combo.setCurrentIndex(0)
        combo.showPopup()
        fixtures.QTest.keyClick(combo.view(), Qt.Key.Key_Down)
        fixtures.QTest.keyClick(combo.view(), Qt.Key.Key_Return)
        self.assertEqual(combo.currentIndex(), 1)
        combo.hidePopup()
        self.window.ui.logPrintText.setPlainText("테스트 로그")
        self.window.ui.logPrintText.selectAll()
        menu = self.window._create_log_context_menu()
        self.addCleanup(menu.deleteLater)
        menu.popup(self.window.ui.logPrintText.mapToGlobal(self.window.ui.logPrintText.rect().center()))
        fixtures.QTest.keyClick(menu, Qt.Key.Key_Down)
        first = menu.activeAction()
        self.assertIsNotNone(first)
        fixtures.QTest.keyClick(menu, Qt.Key.Key_Down)
        self.assertIsNot(menu.activeAction(), first)
        fixtures.QTest.keyClick(menu, Qt.Key.Key_Escape)
        self.assertFalse(menu.isVisible())

    def test_other_menus_keep_selection_and_keyboard_actions_without_inner_frames(self):
        settings = self.window.settings_panel
        for theme in ("light", "dark"):
            settings.theme_combo.setCurrentIndex(settings.theme_combo.findData(theme))
            self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
            settings.navigation.setFocus(Qt.FocusReason.TabFocusReason)
            settings.navigation.setCurrentRow(0)
            fixtures.QTest.keyClick(settings.navigation, Qt.Key.Key_Down)
            self.assertEqual(settings.navigation.currentRow(), 1)
            self.assertNotEqual(settings.navigation.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.window.ui.mainPages.setCurrentWidget(self.window.ui.extraFeaturesTab)
            navigation = self.window.extra_features_panel.navigation
            navigation.setCurrentRow(0)
            navigation.setFocus(Qt.FocusReason.TabFocusReason)
            fixtures.QTest.keyClick(navigation, Qt.Key.Key_Down)
            self.assertEqual(navigation.currentRow(), 1)
            toggle = self.window.extra_features_panel.navigation_toggle
            toggle.setFocus(Qt.FocusReason.TabFocusReason)
            fixtures.QTest.keyClick(toggle, Qt.Key.Key_Space)
            self.assertTrue(toggle.isChecked())
            fixtures.QTest.keyClick(toggle, Qt.Key.Key_Space)
            self.assertFalse(toggle.isChecked())
            self.window.ui.mainPages.setCurrentWidget(self.window.ui.mainTab)
            self.window.show_after_actions()
            control = self.window.after_action_panel.controls["close_maa"]
            before, after = self.focus_image(control)
            self.assertEqual(before, after)
            value = control.isChecked()
            fixtures.QTest.keyClick(control, Qt.Key.Key_Space)
            self.assertNotEqual(control.isChecked(), value)


if __name__ == "__main__":
    unittest.main()
