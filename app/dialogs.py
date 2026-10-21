"""Deletion confirmations follow the app theme without a body question icon."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox


def confirm_deletion(parent, title, text):
    dialog = QMessageBox(parent)
    dialog.setOption(QMessageBox.Option.DontUseNativeDialog)
    dialog.setWindowTitle(title)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(text)
    dialog.setIcon(QMessageBox.Icon.NoIcon)
    dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    dialog.setDefaultButton(QMessageBox.StandardButton.No)
    dialog.setEscapeButton(QMessageBox.StandardButton.No)
    try:
        return QMessageBox.StandardButton(dialog.exec())
    finally:
        dialog.deleteLater()
