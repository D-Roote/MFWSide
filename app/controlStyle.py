"""Suppress native inner focus frames without disabling keyboard interaction."""
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle


class KeyboardControlStyle(QProxyStyle):
    def drawPrimitive(self, element, option, painter, widget=None):
        if element == QStyle.PrimitiveElement.PE_FrameFocusRect:
            return
        super().drawPrimitive(element, option, painter, widget)


def install_control_style():
    application = QApplication.instance()
    if application is None:
        return None
    current = application.style()
    if isinstance(current, KeyboardControlStyle):
        return current
    # Give the proxy its own base style, never ownership of QApplication.style().
    # Keep its Python virtual methods alive for exactly the application's life.
    style = KeyboardControlStyle(current.name())
    style.setParent(application)
    application._mfw_control_style = style
    application.setStyle(style)
    return style
