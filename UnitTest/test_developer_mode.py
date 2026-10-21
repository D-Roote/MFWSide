"""Hidden diagnostic presentation is session-local; no native jobs or apps."""
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import unittest

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QIcon, QKeyEvent, QPixmap
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QMessageBox
from app.dialogs import show_notice
from app.logPresentation import format_log_message
from app.settingsUI import KONAMI_CODE
from test_multi_execution import connection


DIAGNOSTIC = "Agent 1 연결 중입니다. 진단 로그: C:\\Apps With Spaces\\user\\debug\\agent-1.log"
PATH_TEXT = "C:/Apps With Spaces/user/debug/agent-1.log"


class LogPresentationTests(unittest.TestCase):
    def test_normal_errors_and_progress_are_not_filtered(self):
        for text in ("연결에 실패했습니다.", "세션 진단 로그를 확인하세요.", "첫째\n둘째\n", "<custom> 작업"):
            self.assertEqual(format_log_message(text), text)

    def test_default_hides_only_diagnostic_details(self):
        self.assertEqual(format_log_message(DIAGNOSTIC), "Agent 1 연결 중입니다.")
        self.assertEqual(format_log_message("진단로그: C:/debug/session.log"), "")
        self.assertEqual(format_log_message("연결 오류\n진단 로그:\nC:/debug/session.log"), "연결 오류")

    def test_developer_path_has_its_own_full_line_and_is_idempotent(self):
        formatted = format_log_message(DIAGNOSTIC, diagnostics=True)
        self.assertEqual(formatted, f"Agent 1 연결 중입니다.\n진단 로그:\n{PATH_TEXT}")
        self.assertEqual(format_log_message(formatted, diagnostics=True), formatted)
        self.assertEqual(format_log_message(formatted), "Agent 1 연결 중입니다.")


class DeveloperModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.settings = self.window.settings_panel
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        self.focus(self.settings.navigation)

    def focus(self, widget):
        widget.setFocus()
        QApplication.processEvents()
        self.assertIs(QApplication.focusWidget(), widget)

    def keys(self, keys=KONAMI_CODE, target=None):
        for key in keys:
            fixtures.QTest.keyClick(target or self.settings.navigation, key)

    def test_connection_diagnostics_hidden_by_default_and_errors_remain(self):
        self.window.append_log(DIAGNOSTIC)
        self.window.append_log("연결에 실패했습니다.\n진단 로그: C:/debug/failed.log")
        text = self.window.ui.logPrintText.toPlainText()
        self.assertIn("Agent 1 연결 중입니다.", text)
        self.assertIn("연결에 실패했습니다.", text)
        self.assertNotIn("진단 로그:", text)
        self.assertNotIn("C:/", text)
        self.assertNotIn("C:\\", text)
        self.assertFalse(self.window.diagnostic_logs_enabled)

    def test_settings_code_enables_popup_once_without_saving_the_mode(self):
        before = self.settings.store.config_path.read_bytes()
        with patch("app.winUI.show_notice") as information:
            self.keys()
            self.assertTrue(self.window.diagnostic_logs_enabled)
            information.assert_called_once_with(self.window, "개발자 모드", "추가 로그를 표기합니다.")
            self.keys()
            information.assert_called_once()
        self.assertEqual(self.settings.store.config_path.read_bytes(), before)
        self.window.append_log(DIAGNOSTIC)
        self.assertIn(f"진단 로그:\n{PATH_TEXT}", self.window.ui.logPrintText.toPlainText())

    def test_wrong_sequence_and_auto_repeat_do_not_enable_mode(self):
        with patch("app.winUI.show_notice") as information:
            self.keys((*KONAMI_CODE[:-1], Qt.Key.Key_X))
            self.keys(KONAMI_CODE[2:])
            QApplication.sendEvent(self.settings.navigation,
                                  QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up,
                                            Qt.KeyboardModifier.NoModifier, "", True))
            self.keys(KONAMI_CODE[1:])
            information.assert_not_called()
        self.assertFalse(self.window.diagnostic_logs_enabled)

    def test_hidden_settings_and_partial_code_across_tabs_do_not_enable(self):
        self.keys(KONAMI_CODE[:5])
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.mainTab)
        self.focus(self.window.ui.logPrintText)
        self.keys(target=self.window.ui.logPrintText)
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        self.focus(self.settings.navigation)
        self.keys(KONAMI_CODE[5:])
        self.assertFalse(self.window.diagnostic_logs_enabled)

    def test_editing_text_does_not_trigger_mode_or_steal_typing(self):
        editor = self.settings.program_extra_input
        self.focus(editor)
        self.keys(target=editor)
        self.assertEqual(editor.text(), "ba")
        self.assertFalse(self.window.diagnostic_logs_enabled)

    def test_multi_instance_log_and_tooltip_follow_the_same_visibility_policy(self):
        panel = self.window.multi_panel
        sid = panel.manager.add({**connection(), "preset": "Win32PrintWindow"})
        session = panel.manager.sessions[sid]
        session.logs.append(DIAGNOSTIC)
        panel._log(sid, DIAGNOSTIC)
        panel.cards[sid].refresh()
        self.assertNotIn("진단 로그:", panel.cards[sid].status.toolTip())
        self.assertNotIn(PATH_TEXT, self.window.ui.logPrintText.toPlainText())
        before = self.window.ui.logPrintText.toPlainText()
        panel._log(sid, "진단 로그: C:/debug/only.log")
        self.assertEqual(self.window.ui.logPrintText.toPlainText(), before)
        with patch("app.winUI.show_notice"):
            self.keys()
        panel._log(sid, DIAGNOSTIC)
        panel.cards[sid].refresh()
        self.assertIn(f"진단 로그:\n{PATH_TEXT}", panel.cards[sid].status.toolTip())
        self.assertIn(f"진단 로그:\n{PATH_TEXT}", self.window.ui.logPrintText.toPlainText())

    def test_developer_popup_uses_shared_body_and_title_bar_theme(self):
        icon = QPixmap(16, 16)
        icon.fill(Qt.GlobalColor.red)
        self.window.setWindowIcon(QIcon(icon))
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                combo = self.settings.theme_combo
                combo.setCurrentIndex(combo.findData(theme))
                self.window.diagnostic_logs_enabled = False

                def execute(dialog):
                    self.assertTrue(dialog.testOption(QMessageBox.Option.DontUseNativeDialog))
                    self.assertEqual(dialog.icon(), QMessageBox.Icon.NoIcon)
                    self.assertEqual(dialog.windowTitle(), "개발자 모드")
                    self.assertEqual(dialog.text(), "추가 로그를 표기합니다.")
                    self.assertEqual(dialog.textFormat(), Qt.TextFormat.PlainText)
                    self.assertEqual(dialog.windowIcon().cacheKey(), self.window.windowIcon().cacheKey())
                    self.assertEqual(dialog.standardButtons(), QMessageBox.StandardButton.Ok)
                    self.assertEqual(dialog.defaultButton(), dialog.button(QMessageBox.StandardButton.Ok))
                    self.assertEqual(dialog.escapeButton(), dialog.button(QMessageBox.StandardButton.Ok))
                    dialog.show()
                    fixtures.QTest.qWait(10)
                    caption.assert_called_once_with(
                        int(dialog.winId()), self.window._title_bar_theme,
                        system_color_scheme=self.window._style_hints.colorScheme())
                    self.assertEqual(dialog.palette().window().color().name(),
                                     "#172238" if theme == "dark" else "#ffffff")
                    dialog.close()
                    return QMessageBox.StandardButton.Ok

                with patch("app.winUI.apply_windows_title_bar_theme") as caption, \
                        patch.object(QMessageBox, "exec", new=execute):
                    self.window._enable_developer_mode()
                self.assertTrue(self.window.diagnostic_logs_enabled)

    def test_notice_title_and_contents_are_centered_without_changing_application_name(self):
        application = QApplication.instance()
        old_name = application.applicationDisplayName()
        application.setApplicationDisplayName("MFWSide")
        self.addCleanup(lambda: application.setApplicationDisplayName(old_name))
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                combo = self.settings.theme_combo
                combo.setCurrentIndex(combo.findData(theme))

                def execute(dialog):
                    self.assertEqual(dialog.windowTitle(), "개발자 모드")
                    dialog.show()
                    fixtures.QTest.qWait(10)
                    label = dialog.findChild(QLabel, "qt_msgbox_label")
                    buttons = dialog.findChild(QDialogButtonBox)
                    self.assertEqual(label.alignment(), Qt.AlignmentFlag.AlignCenter)
                    self.assertTrue(buttons.centerButtons())
                    center = dialog.rect().center().x()
                    label_center = label.mapTo(dialog, label.rect().center()).x()
                    button = dialog.button(QMessageBox.StandardButton.Ok)
                    button_center = button.mapTo(dialog, button.rect().center()).x()
                    self.assertLessEqual(abs(label_center - center), 1)
                    self.assertLessEqual(abs(button_center - center), 1)
                    caption.assert_called_once_with(dialog)
                    self.assertEqual(application.applicationDisplayName(), "MFWSide")
                    self.assertEqual(self.window.windowTitle(), "MFWSide")
                    dialog.close()
                    return QMessageBox.StandardButton.Ok

                with patch("app.dialogs._set_native_caption") as caption, \
                        patch.object(QMessageBox, "exec", new=execute):
                    show_notice(self.window, "개발자 모드 - MFWSide", "추가 로그를 표기합니다.")

    @unittest.skipUnless(sys.platform == "win32", "Native caption check requires Windows")
    def test_windows_caption_override_is_local_to_a_hidden_dialog(self):
        # A fresh Qt Windows process creates HWNDs but never shows a window or
        # imports MaaFW. Check the actual OS caption, not just windowTitle().
        code = '''
import ctypes
from ctypes import wintypes
from PySide6.QtWidgets import QApplication, QWidget
from app.dialogs import AppMessageBox, _set_native_caption
app = QApplication([])
app.setApplicationDisplayName("MFWSide")
main = QWidget()
main.setWindowTitle("Main")
dialog = AppMessageBox(main, compact_title=True)
dialog.setWindowTitle("개발자 모드")
user = ctypes.WinDLL("user32", use_last_error=True)
user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user.GetWindowTextW.restype = ctypes.c_int
def title(widget):
    text = ctypes.create_unicode_buffer(256)
    user.GetWindowTextW(int(widget.winId()), text, len(text))
    return text.value
assert title(dialog) == "개발자 모드 - MFWSide", repr(title(dialog))
assert _set_native_caption(dialog)
assert title(dialog) == "개발자 모드", repr(title(dialog))
assert title(main) == "Main - MFWSide", repr(title(main))
assert app.applicationDisplayName() == "MFWSide"
dialog.destroy()
main.destroy()
'''
        result = subprocess.run([sys.executable, "-c", code],
                                cwd=Path(__file__).resolve().parents[1],
                                env={**os.environ, "QT_QPA_PLATFORM": "windows"},
                                capture_output=True, text=True, timeout=15,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
