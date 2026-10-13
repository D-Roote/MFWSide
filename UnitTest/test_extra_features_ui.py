"""Additional workspace navigation, collapsible sidebar and UI-only persistence."""
import json
import unittest
from unittest.mock import patch

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget


class ExtraFeaturesUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.ui = self.window.ui
        self.panel = self.window.extra_features_panel
        self.window.show()
        QApplication.processEvents()

    def open_extras(self):
        self.ui.extraFeaturesNavButton.click()
        QApplication.processEvents()

    def test_header_and_page_order_are_start_extras_settings_with_exclusive_selection(self):
        header = self.ui.appHeader.layout()
        labels = [header.itemAt(i).widget().text() for i in range(header.count())
                  if isinstance(header.itemAt(i).widget(), QPushButton)]
        self.assertEqual(labels, ["시작", "추가 기능", "설정"])
        self.assertEqual([self.ui.mainPages.widget(i) for i in range(3)],
                         [self.ui.mainTab, self.ui.extraFeaturesTab, self.ui.settingTab])
        for button, page in ((self.ui.extraFeaturesNavButton, self.ui.extraFeaturesTab),
                             (self.ui.settingsNavButton, self.ui.settingTab),
                             (self.ui.dashboardNavButton, self.ui.mainTab)):
            button.click()
            self.assertIs(self.ui.mainPages.currentWidget(), page)
            self.assertEqual(sum(b.isChecked() for b in self.window.page_navigation.buttons()), 1)
            self.assertTrue(button.isChecked())

    def test_programmatic_navigation_updates_header_without_runtime_work(self):
        runtime = self.window.runtime
        runtime.reset_mock()
        self.ui.mainPages.setCurrentWidget(self.ui.extraFeaturesTab)
        self.assertTrue(self.ui.extraFeaturesNavButton.isChecked())
        self.assertFalse(self.ui.dashboardNavButton.isChecked())
        runtime.initialize.assert_not_called()
        runtime.run_task.assert_not_called()
        runtime.capture_preview_frame.assert_not_called()
        self.assertFalse(self.window.monitor.streaming)
        self.assertIsNone(self.window.monitor.worker)

    def test_sidebar_bar_hides_list_and_reclaims_width_without_losing_selected_page(self):
        self.open_extras()
        original = self.panel.pages.width()
        selected = self.panel.pages.currentWidget()
        self.panel.navigation_toggle.click()
        QApplication.processEvents()
        self.assertTrue(self.panel.navigation_collapsed)
        self.assertTrue(self.panel.navigation.isHidden())
        self.assertTrue(self.panel.navigation_toggle.isVisible())
        self.assertEqual(self.panel.navigation_toggle.width(), 18)
        self.assertGreater(self.panel.pages.width(), original + 200)
        self.assertIs(self.panel.pages.currentWidget(), selected)
        self.assertEqual(self.panel.navigation_toggle.accessibleName(), "추가 기능 목록 펼치기")
        self.panel.navigation_toggle.click()
        QApplication.processEvents()
        self.assertFalse(self.panel.navigation_collapsed)
        self.assertTrue(self.panel.navigation.isVisible())
        self.assertEqual(self.panel.pages.width(), original)
        self.assertEqual(self.panel.navigation_toggle.accessibleName(), "추가 기능 목록 접기")

    def test_sidebar_bar_supports_keyboard_toggle(self):
        self.open_extras()
        self.window.activateWindow()
        self.panel.navigation_toggle.setFocus()
        QTest.keyClick(self.panel.navigation_toggle, Qt.Key.Key_Space)
        self.assertTrue(self.panel.navigation_collapsed)
        self.assertTrue(self.panel.navigation_toggle.hasFocus())
        QTest.keyClick(self.panel.navigation_toggle, Qt.Key.Key_Space)
        self.assertFalse(self.panel.navigation_collapsed)

    def test_feature_registration_shows_corresponding_scrollable_contents(self):
        contents = QWidget()
        layout = QVBoxLayout(contents)
        label = QLabel("테스트 기능 내용", contents)
        layout.addWidget(label)
        index = self.panel.add_feature_page("테스트 기능", contents)
        self.assertEqual(index, 2)
        self.open_extras()
        self.panel.navigation.setCurrentRow(index)
        QApplication.processEvents()
        self.assertIs(self.panel.pages.currentWidget().widget(), contents)
        self.assertTrue(label.isVisible())
        self.assertIsNotNone(self.panel.pages.currentWidget().verticalScrollBar()._rounded_paint_filter)
        self.panel.set_navigation_collapsed(True)
        self.assertEqual(self.panel.navigation.currentRow(), index)
        self.assertIs(self.panel.pages.currentWidget().widget(), contents)

    def test_default_is_only_an_honest_placeholder_without_business_actions(self):
        self.assertEqual(self.panel.navigation.count(), 2)
        self.assertEqual(self.panel.navigation.item(0).text(), "다중 실행")
        self.assertEqual(self.panel.navigation.item(1).text(), "안내")
        label = self.panel.findChild(QLabel, "settingsSectionDescription")
        self.assertIn("아직 등록된 추가 기능이 없습니다.", label.text())
        self.assertIs(self.ui.mainPages.currentWidget(), self.ui.mainTab)

    def test_light_dark_surfaces_and_minimum_width_fit_after_collapse(self):
        self.window.resize(1100, 650)
        self.open_extras()
        self.panel.navigation.setCurrentRow(1)
        card = self.panel.findChild(QWidget, "extraFeatureCard")
        for theme, card_color, bar_color in (("light", "#ffffff", "#e2e8f0"),
                                             ("dark", "#172238", "#30415e")):
            self.window.set_title_bar_theme(theme)
            for collapsed in (False, True):
                with self.subTest(theme=theme, collapsed=collapsed):
                    self.panel.set_navigation_collapsed(collapsed)
                    QApplication.processEvents()
                    self.assertEqual(card.grab().toImage().pixelColor(10, 10).name(), card_color)
                    toggle = self.panel.navigation_toggle
                    toggle.clearFocus()
                    self.assertEqual(toggle.grab().toImage().pixelColor(8, 12).name(), bar_color)
                    viewport = self.panel.detail_scroll.viewport()
                    self.assertLessEqual(card.width(), viewport.width())
                    self.assertEqual(self.panel.detail_scroll.horizontalScrollBar().maximum(), 0)

    def test_sidebar_preference_survives_restart_but_start_tab_remains_default(self):
        self.open_extras()
        self.panel.set_navigation_collapsed(True)
        self.window.save_user_config()
        path = self.window.runtime.user_dir / "config" / "user_config.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertIs(saved["ui_state"]["extra_navigation_collapsed"], True)
        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            reopened = fixtures.MainWindow()
        self.addCleanup(reopened.deleteLater)
        self.assertTrue(reopened.extra_features_panel.navigation_collapsed)
        self.assertIs(reopened.ui.mainPages.currentWidget(), reopened.ui.mainTab)
        self.assertFalse(reopened.monitor.streaming)

    def test_invalid_sidebar_preference_is_ignored(self):
        self.window._saved_ui_state = {"extra_navigation_collapsed": "false"}
        self.window._restore_ui_state()
        self.assertFalse(self.panel.navigation_collapsed)


if __name__ == "__main__":
    unittest.main()
