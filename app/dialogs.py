"""Shared application dialogs with themed captions and no stock body icons."""
import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QLabel, QMessageBox, QSizePolicy


def _set_native_caption(dialog):
    # Qt's Windows plugin unconditionally appends applicationDisplayName to
    # native captions. Override only this dialog, never the global app name.
    if sys.platform != "win32":
        return False
    try:
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user.SetWindowTextW.restype = wintypes.BOOL
        return bool(user.SetWindowTextW(int(dialog.winId()), dialog.windowTitle()))
    except OSError:
        return False


class AppMessageBox(QMessageBox):
    def __init__(self, parent=None, *, centered=False, compact_title=False):
        super().__init__(parent)
        self._centered = centered
        self._compact_title = compact_title

    def align_contents(self):
        if not self._centered:
            return
        label = self.findChild(QLabel, "qt_msgbox_label")
        if label is not None:
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        buttons = self.findChild(QDialogButtonBox)
        if buttons is not None:
            buttons.setCenterButtons(True)
        # NoIcon still reserves a leading spacer in Qt 6.11's message layout.
        # Remove its width so the text is centered in the window, not offset.
        layout = self.layout()
        layout.setHorizontalSpacing(0)
        item = layout.itemAtPosition(0, 0)
        spacer = item.spacerItem() if item is not None else None
        if spacer is not None and spacer.sizeHint().width():
            spacer.changeSize(0, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            layout.invalidate()

    def showEvent(self, event):
        super().showEvent(event)
        self.align_contents()
        parent = self.parentWidget()
        owner = parent.window() if parent is not None else None
        apply_theme = getattr(owner, "_apply_dialog_title_bar_theme", None)
        if callable(apply_theme):
            apply_theme(self)
        if self._compact_title:
            _set_native_caption(self)


def _show_message(parent, title, text, buttons, default, *, centered=False, compact_title=False,
                  right_padding=0):
    dialog = AppMessageBox(parent, centered=centered, compact_title=compact_title)
    dialog.setOption(QMessageBox.Option.DontUseNativeDialog)
    dialog.setWindowTitle(title.partition(" - ")[0] if compact_title else title)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(text)
    dialog.setIcon(QMessageBox.Icon.NoIcon)
    dialog.setStandardButtons(buttons)
    dialog.setDefaultButton(default)
    dialog.setEscapeButton(default)
    if right_padding:
        dialog.ensurePolished()
        label = dialog.findChild(QLabel, "qt_msgbox_label")
        if label is not None:
            label.ensurePolished()
            margins = label.contentsMargins()
            label.setContentsMargins(margins.left(), margins.top(),
                                     margins.right() + right_padding, margins.bottom())
            reference_width = label.fontMetrics().horizontalAdvance(text.partition("\n")[0])
            label.setMinimumWidth(reference_width + margins.left() + margins.right() + right_padding)
    dialog.align_contents()
    try:
        return QMessageBox.StandardButton(dialog.exec())
    finally:
        dialog.deleteLater()


def confirm_deletion(parent, title, text, *, right_padding=0):
    return _show_message(parent, title, text,
                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                         QMessageBox.StandardButton.No, right_padding=right_padding)


def show_notice(parent, title, text):
    return _show_message(parent, title, text,
                         QMessageBox.StandardButton.Ok, QMessageBox.StandardButton.Ok,
                         centered=True, compact_title=True)
