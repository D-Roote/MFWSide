"""Resource labels/title are independent; missing fields use MFWSide."""
import unittest

import test_runtime_lifecycle as fixtures
from app.winUI import interface_titles


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


if __name__ == "__main__":
    unittest.main()
