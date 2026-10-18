"""Countdown rendering, layout, and application-modal lifecycle without launches."""
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import QPoint
from app.startupUI import AppStartupDialog


class StartupDialogUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.window.resize(self.window.minimumWidth(), 820)
        self.window.show()
        fixtures.QApplication.processEvents()
        self.dialog = AppStartupDialog(self.window)
        self.addCleanup(self.dialog.hide)
        self.addCleanup(self.dialog.deleteLater)

    def test_countdown_handles_hours_and_leading_zeroes(self):
        for seconds, expected in ((3600, ["01", "00", "00"]), (65, ["00", "01", "05"]),
                                  (0, ["00", "00", "00"])):
            with self.subTest(seconds=seconds):
                self.dialog.set_remaining(seconds)
                self.assertEqual([dial.value_label.text() for dial in self.dialog.dials], expected)
                self.assertTrue(self.dialog.start_button.isEnabled())

    def test_dialog_is_centered_or_clamped_to_its_screen(self):
        self.dialog.set_remaining(30)
        self.dialog.show()
        fixtures.QApplication.processEvents()
        bounds = self.window.screen().availableGeometry()
        expected = self.dialog.frameGeometry()
        expected.moveCenter(self.window.frameGeometry().center())
        self.assertEqual(self.dialog.pos(), QPoint(
            max(bounds.left(), min(expected.left(), bounds.right() - expected.width() + 1)),
            max(bounds.top(), min(expected.top(), bounds.bottom() - expected.height() + 1))))

    def test_countdown_and_controls_fit_in_both_themes(self):
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                panel = self.window.settings_panel
                panel.theme_combo.setCurrentIndex(panel.theme_combo.findData(theme))
                self.dialog.set_remaining(65)
                self.dialog.show()
                fixtures.QTest.qWait(20)
                self.assertEqual(self.window.width(), self.window.minimumWidth())
                self.assertLess(self.dialog.width(), self.window.width())
                self.assertLess(self.dialog.height(), self.window.height())
                for dial in self.dialog.dials:
                    label = dial.value_label
                    self.assertGreaterEqual(label.width(), label.fontMetrics().horizontalAdvance(label.text()))
                    self.assertGreaterEqual(label.height(), label.fontMetrics().height())
                    self.assertTrue(self.dialog.rect().contains(dial.mapTo(self.dialog, dial.rect().bottomRight())))
                for button in (self.dialog.cancel_button, self.dialog.start_button):
                    self.assertGreater(button.width(), button.fontMetrics().horizontalAdvance(button.text()))
                self.assertTrue(self.dialog.grab().toImage().pixelColor(250, 200).isValid())

    def test_preparing_and_cancelling_cannot_start_or_dismiss_silently(self):
        self.dialog.set_remaining(20, preparing=True)
        self.assertFalse(self.dialog.start_button.isEnabled())
        cancellations = []
        self.dialog.cancel_requested.connect(lambda: cancellations.append(True))
        self.dialog.show()
        self.dialog.close()
        self.assertEqual(cancellations, [True])
        self.assertTrue(self.dialog.isVisible())
        self.dialog.set_cancelling()
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertFalse(self.dialog.cancel_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
