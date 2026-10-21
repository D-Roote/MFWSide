"""Deletion dialog appearance and cancel behavior; no project files are deleted."""
import os
from pathlib import Path
from unittest.mock import patch
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QMessageBox
from app.dialogs import confirm_deletion


class DeleteConfirmationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window

    def test_no_body_icon_preserves_title_icon_and_safe_defaults_in_both_themes(self):
        icon = QPixmap(16, 16)
        icon.fill(Qt.GlobalColor.red)
        self.window.setWindowIcon(QIcon(icon))
        for theme in ("light", "dark"):
            self.window.settings_panel.theme_combo.setCurrentIndex(
                self.window.settings_panel.theme_combo.findData(theme))
            for title in ("프리셋 삭제", "로그 영구 삭제"):
                with self.subTest(theme=theme, title=title):
                    def execute(dialog):
                        self.assertEqual(dialog.windowTitle(), title)
                        self.assertEqual(dialog.icon(), QMessageBox.Icon.NoIcon)
                        self.assertTrue(dialog.testOption(QMessageBox.Option.DontUseNativeDialog))
                        self.assertEqual(dialog.textFormat(), Qt.TextFormat.PlainText)
                        self.assertEqual(dialog.defaultButton(), dialog.button(QMessageBox.StandardButton.No))
                        self.assertEqual(dialog.escapeButton(), dialog.button(QMessageBox.StandardButton.No))
                        image = dialog.windowIcon().pixmap(16, 16).toImage()
                        self.assertFalse(image.isNull())
                        self.assertEqual(dialog.windowIcon().cacheKey(), self.window.windowIcon().cacheKey())
                        self.assertEqual(image.pixelColor(image.width() // 2, image.height() // 2),
                                         Qt.GlobalColor.red)
                        dialog.show()
                        fixtures.QTest.qWait(10)
                        directory = os.environ.get("MAABA_UI_PREVIEW_DIR")
                        if directory:
                            Path(directory).mkdir(parents=True, exist_ok=True)
                            self.assertTrue(dialog.grab().save(str(Path(directory) / f"delete_{theme}_{title}.png")))
                        dialog.close()
                        return QMessageBox.StandardButton.No
                    with patch.object(QMessageBox, "exec", new=execute):
                        result = confirm_deletion(self.window, title,
                                                  "복구할 수 없습니다.\n\n계속하시겠습니까?")
                    self.assertEqual(result, QMessageBox.StandardButton.No)

    def test_only_explicit_yes_is_accepted(self):
        for result in (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No,
                       QMessageBox.StandardButton.NoButton):
            with self.subTest(result=result), patch.object(QMessageBox, "exec", return_value=result):
                self.assertEqual(confirm_deletion(self.window, "프리셋 삭제", "삭제할까요?"), result)


if __name__ == "__main__":
    unittest.main()
