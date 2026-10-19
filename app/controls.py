"""Shared small controls for explicit, safe configuration changes."""
from PySide6.QtWidgets import QComboBox, QSpinBox, QTimeEdit
from PySide6.QtGui import QValidator


class PopupOnlyWheelComboBox(QComboBox):
    """Ignore wheel selection while closed, even when the combo has focus."""

    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            # Let the enclosing scroll area handle scrolling.
            event.ignore()


class NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event):
        event.ignore()


class NoWheelTimeEdit(QTimeEdit):
    def wheelEvent(self, event):
        event.ignore()


class ClampedSecondsSpinBox(NoWheelSpinBox):
    """Accept typed numbers above the limit, then visibly clamp instead of rejecting digits."""
    LIMIT = 3600

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, self.LIMIT)
        self.setKeyboardTracking(False)
        self.lineEdit().textEdited.connect(self._clamp_typed_value)

    def _digits(self, text):
        if self.prefix() and text.startswith(self.prefix()):
            text = text[len(self.prefix()):]
        if self.suffix() and text.endswith(self.suffix()):
            text = text[:-len(self.suffix())]
        return text.strip()

    def validate(self, text, position):
        digits = self._digits(text)
        state = (QValidator.State.Intermediate if not digits else QValidator.State.Acceptable
                 if digits.isascii() and digits.isdecimal() else QValidator.State.Invalid)
        return state, text, position

    def valueFromText(self, text):
        digits = self._digits(text).lstrip("0") or "0"
        if not digits.isascii() or not digits.isdecimal():
            return 0
        # Avoid converting arbitrarily large pasted integers.
        return self.LIMIT if len(digits) > 4 else min(self.LIMIT, int(digits))

    def fixup(self, text):
        return f"{self.prefix()}{self.valueFromText(text)}{self.suffix()}"

    def _clamp_typed_value(self, text):
        digits = self._digits(text).lstrip("0") or "0"
        if digits.isascii() and digits.isdecimal() and (len(digits) > 4 or int(digits) > self.LIMIT):
            self.setValue(self.LIMIT)
            self.lineEdit().setText(f"{self.prefix()}{self.LIMIT}{self.suffix()}")
            self.lineEdit().setCursorPosition(len(self.prefix()) + len(str(self.LIMIT)))
