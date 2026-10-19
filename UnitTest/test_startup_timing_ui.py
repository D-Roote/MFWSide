"""Compact paired timing fields, overflow clamping, persistence and wheel safety."""
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtGui import QValidator
from PySide6.QtCore import Qt
from app.startupSettings import normalize_startup_config


class StartupTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.panel = self.window.settings_panel
        target = self.window.runtime.user_dir / "client.exe"
        target.touch()
        self.panel.program_path_input.setText(str(target))
        self.panel._apply_program_path()
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        fixtures.QApplication.processEvents()

    def test_fields_share_compact_width_and_keep_internal_delay_unadvertised(self):
        self.assertEqual(self.panel.auto_start_wait_spin.width(), self.panel.daily_restart_time.width())
        self.assertGreaterEqual(self.panel.auto_start_wait_spin.width(), 110)
        self.assertLessEqual(self.panel.auto_start_wait_spin.width(), 130)
        self.assertNotIn("1분", self.panel.daily_restart_time.toolTip())
        self.assertNotIn("1분", self.panel.startup_rows["daily_restart"].description_label.text())
        self.assertFalse(hasattr(self.panel, "auto_start_wait_row"))

    def test_overflow_digits_are_accepted_and_typing_visibly_clamps_to_limit(self):
        spin = self.panel.auto_start_wait_spin
        self.assertEqual(spin.validate("9999 초", 4)[0], QValidator.State.Acceptable)
        editor = spin.lineEdit()
        editor.setFocus()
        editor.selectAll()
        fixtures.QTest.keyClicks(editor, "9999")
        self.assertEqual(spin.value(), 3600)
        self.assertEqual(editor.text(), "3600 초")
        saved = json.loads(self.panel.store.config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["startup"]["task_wait_seconds"], 3600)

    def test_edit_commit_clamps_pasted_and_very_large_values(self):
        spin = self.panel.auto_start_wait_spin
        for value, expected in (("3601", 3600), ("999999999999999999999", 3600),
                                ("00003600", 3600), ("0000", 0), ("135", 135)):
            with self.subTest(value=value):
                spin.lineEdit().setText(value + " 초")
                spin.interpretText()
                self.assertEqual(spin.value(), expected)
                self.assertEqual(spin.lineEdit().text(), f"{expected} 초")
        self.assertEqual(normalize_startup_config({"task_wait_seconds": 999999})["task_wait_seconds"], 3600)

    def test_normal_typed_value_commits_and_restores_without_a_separate_row(self):
        spin = self.panel.auto_start_wait_spin
        editor = spin.lineEdit()
        editor.setFocus()
        editor.selectAll()
        fixtures.QTest.keyClicks(editor, "234")
        fixtures.QTest.keyClick(editor, Qt.Key.Key_Return)
        self.assertEqual(spin.value(), 234)
        self.assertEqual(self.panel.startup_settings()["task_wait_seconds"], 234)
        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            restored = fixtures.MainWindow()
        self.addCleanup(restored.deleteLater)
        self.assertEqual(restored.settings_panel.auto_start_wait_spin.value(), 234)
        self.assertFalse(hasattr(restored.settings_panel, "auto_start_wait_row"))

    def test_compound_row_labels_still_toggle_the_checkbox(self):
        checkbox = self.panel.startup_checkboxes["auto_start_tasks"]
        self.panel.startup_rows["auto_start_tasks"].title_label.activated.emit()
        self.assertTrue(checkbox.isChecked())
        self.panel.startup_rows["auto_start_tasks"].description_label.activated.emit()
        self.assertFalse(checkbox.isChecked())

    def test_fields_fit_minimum_width_and_align_in_both_themes(self):
        self.window.resize(self.window.minimumWidth(), 820)
        for theme in ("light", "dark"):
            self.panel.theme_combo.setCurrentIndex(self.panel.theme_combo.findData(theme))
            self.panel.navigation.setCurrentRow(self.panel.section_order().index("program"))
            self.panel.auto_start_wait_spin.setValue(3600)
            fixtures.QApplication.processEvents()
            auto = self.panel.auto_start_wait_spin
            daily = self.panel.daily_restart_time
            self.assertEqual(auto.mapTo(self.panel, auto.rect().topLeft()).x(),
                             daily.mapTo(self.panel, daily.rect().topLeft()).x())
            self.assertLess(auto.fontMetrics().horizontalAdvance("3600 초"), auto.lineEdit().width())
            self.assertEqual(self.panel.detail_scroll.horizontalScrollBar().maximum(), 0)
            self.assertEqual(self.window.width(), self.window.minimumWidth())
            directory = os.environ.get("MAABA_UI_PREVIEW_DIR")
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
                self.panel.grab().save(str(Path(directory) / f"compact_startup_{theme}.png"))

    def test_wheel_does_not_change_either_time_field(self):
        for editor in (self.panel.auto_start_wait_spin, self.panel.daily_restart_time):
            event = MagicMock()
            editor.wheelEvent(event)
            event.ignore.assert_called_once()
