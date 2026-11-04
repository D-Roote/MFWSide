"""Exercise real Qt icon decoding/inheritance using temporary image fixtures."""
from pathlib import Path
import sys
import tempfile
import unittest

from PySide6.QtGui import QColor, QIcon, QImage
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.appIcon import apply_application_icon
from app.paths import ApplicationPaths, resolve_application_paths


class ApplicationIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = ApplicationPaths(self.root, self.root / "app")
        previous = self.application.windowIcon()
        self.addCleanup(self.application.setWindowIcon, previous)
        self.application.setWindowIcon(QIcon())

    def create_icon(self, paths):
        path = paths.ui_resource_dir / "appIcon/icon.ico"
        path.parent.mkdir(parents=True)
        image = QImage(32, 32, QImage.Format.Format_ARGB32)
        image.fill(QColor("#238ab3"))
        self.assertTrue(image.save(str(path), "ICO"))
        return path

    def test_valid_icon_is_inherited_by_main_window_and_dialogs(self):
        self.create_icon(self.paths)
        self.assertTrue(apply_application_icon(self.application, self.paths))
        window = QMainWindow()
        dialog = QMessageBox(window)
        try:
            expected = self.application.windowIcon().cacheKey()
            self.assertFalse(window.windowIcon().pixmap(16, 16).isNull())
            self.assertEqual(window.windowIcon().cacheKey(), expected)
            self.assertEqual(dialog.windowIcon().cacheKey(), expected)
        finally:
            dialog.deleteLater()
            window.deleteLater()

    def test_missing_or_invalid_icon_preserves_the_current_default(self):
        path = self.create_icon(self.paths)
        self.application.setWindowIcon(QIcon(str(path)))
        expected = self.application.windowIcon().cacheKey()
        absent = ApplicationPaths(self.root / "absent", self.root / "absent/app")
        self.assertFalse(apply_application_icon(self.application, absent))
        self.assertEqual(self.application.windowIcon().cacheKey(), expected)
        path.write_bytes(b"invalid icon")
        self.assertFalse(apply_application_icon(self.application, self.paths))
        self.assertEqual(self.application.windowIcon().cacheKey(), expected)

    def test_frozen_icon_comes_from_the_bundle_not_external_resources(self):
        paths = resolve_application_paths(self.root / "install/_internal/app", frozen=True,
                                          executable=self.root / "install/MFWSide.exe")
        self.create_icon(paths)
        self.assertTrue(apply_application_icon(self.application, paths))
        self.assertFalse((paths.root_dir / "app").exists())
