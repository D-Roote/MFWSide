"""Resource labels/title are independent; missing fields use MFWSide."""
import unittest
from unittest.mock import patch

import test_runtime_lifecycle as fixtures
from app.winUI import interface_titles
from app.identity import log_filename_stem


class InterfaceTitleTests(unittest.TestCase):
    def test_label_and_title_are_independent(self):
        self.assertEqual(interface_titles({"label": " 프로젝트 ", "title": "창 제목"}), ("프로젝트", "창 제목"))
        self.assertEqual(interface_titles({"label": "프로젝트"}), ("프로젝트", "MFWSide"))
        self.assertEqual(interface_titles({"title": "창 제목"}), ("MFWSide", "창 제목"))

    def test_missing_invalid_and_blank_values_use_client_fallback(self):
        for data in (None, [], {}, {"name": "Legacy", "custom_title": "Old"},
                     {"label": " \n", "title": ""}, {"label": 12, "title": False}):
            with self.subTest(data=data):
                self.assertEqual(interface_titles(data), ("MFWSide", "MFWSide"))

    def test_log_names_cannot_create_paths_or_invalid_windows_names(self):
        self.assertEqual(log_filename_stem('A/B:C\\D*?<>|"'), "A_B_C_D______")
        self.assertEqual(log_filename_stem("..."), "MFWSide")
        self.assertEqual(log_filename_stem(None), "MFWSide")
        self.assertLessEqual(len(log_filename_stem("긴 제목" * 100)), 80)


class InterfaceTitleUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window

    def test_default_and_resource_branding_are_applied_to_window_and_header(self):
        self.assertEqual(self.window.windowTitle(), "MFWSide")
        self.assertEqual(self.window.ui.appBrandLabel.text(), "MFWSide")
        self.window.runtime.interface.update(label="리소스 이름", title="리소스 창")
        self.window._apply_interface_titles()
        self.assertEqual(self.window.windowTitle(), "리소스 창")
        self.assertEqual(self.window.ui.appBrandLabel.text(), "리소스 이름")
        self.window.resize(self.window.minimumWidth(), 700)
        self.window.show()
        fixtures.QApplication.processEvents()
        label = self.window.ui.appBrandLabel
        self.assertTrue(label.isVisible())
        self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()))

    def test_long_and_markup_like_labels_remain_plain_and_do_not_expand_window(self):
        self.window.runtime.interface.update(label="<b>프로젝트</b>" * 25, title="창")
        self.window._apply_interface_titles()
        self.window.resize(self.window.minimumWidth(), 700)
        self.window.show()
        fixtures.QApplication.processEvents()
        self.assertEqual(self.window.width(), self.window.minimumWidth())
        self.assertEqual(self.window.ui.appBrandLabel.textFormat(), fixtures.Qt.TextFormat.PlainText)
        self.assertEqual(self.window.ui.appBrandLabel.toolTip(), "<b>프로젝트</b>" * 25)
        label = self.window.ui.appBrandLabel
        self.assertGreater(label.width(), 100)
        self.assertLess(label.geometry().right(), self.window.ui.dashboardNavButton.geometry().left())

    def test_startup_and_completion_use_title_not_resource_label(self):
        window = self.window
        window.runtime.interface.update(label="리소스 표시 이름", title="별도 실행 앱")
        window._apply_interface_titles()
        panel = window.settings_panel
        self.assertIn("다음 별도 실행 앱 실행", panel.startup_section.description_label.text())
        for row in panel.startup_rows.values():
            self.assertIn("별도 실행 앱", row.title_label.text())
            self.assertNotIn("MAABA", row.title_label.text())
        window.after_actions.update("close_maa", True)
        window.show_after_actions()
        self.assertEqual(window.after_action_panel.controls["close_maa"].text(), "별도 실행 앱 종료")
        self.assertEqual(window.ui.endStatusLabel.text(), "별도 실행 앱 종료")
        window.runtime.interface.pop("title")
        window._apply_interface_titles()
        self.assertEqual(window.after_action_panel.controls["close_maa"].text(), "MFWSide 종료")
        self.assertIn("MFWSide", panel.startup_rows["launch_on_login"].title_label.text())

    def test_log_save_dialog_uses_sanitized_resource_title(self):
        window = self.window
        window.runtime.interface.update(title="App:Name/Logs")
        window._apply_interface_titles()
        with patch("app.winUI.QFileDialog.getSaveFileName", return_value=("", "")) as dialog:
            self.assertFalse(window.save_log())
        self.assertTrue(dialog.call_args.args[2].startswith("App_Name_Logs-log-"))

    def test_long_and_markup_like_titles_do_not_expand_settings_in_either_theme(self):
        window = self.window
        window.runtime.interface.update(title="<b>긴앱제목</b>" * 30)
        window._apply_interface_titles()
        window.ui.mainPages.setCurrentWidget(window.ui.settingTab)
        window.settings_panel.navigation.setCurrentRow(window.settings_panel.section_order().index("program"))
        window.resize(window.minimumWidth(), 820)
        window.show()
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                panel = window.settings_panel
                panel.theme_combo.setCurrentIndex(panel.theme_combo.findData(theme))
                fixtures.QApplication.processEvents()
                self.assertEqual(window.width(), window.minimumWidth())
                self.assertEqual(panel.detail_scroll.horizontalScrollBar().maximum(), 0)
                for row in panel.startup_rows.values():
                    self.assertEqual(row.title_label.textFormat(), fixtures.Qt.TextFormat.PlainText)


if __name__ == "__main__":
    unittest.main()
