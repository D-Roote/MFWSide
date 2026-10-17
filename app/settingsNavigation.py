"""Stable settings IDs and internal-only section reordering."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QListWidget


SETTINGS_SECTIONS = (
    ("general", "일반"), ("program", "시작 설정"),
    ("controller", "컨트롤러"), ("appearance", "외관"),
    ("management", "관리"),
)


def normalize_section_order(value):
    defaults = [key for key, _label in SETTINGS_SECTIONS]
    result = []
    for key in value if isinstance(value, list) else []:
        if isinstance(key, str) and key in defaults and key not in result:
            result.append(key)
    for index, key in enumerate(defaults):
        if key not in result:
            previous = next((candidate for candidate in reversed(defaults[:index]) if candidate in result), None)
            result.insert(result.index(previous) + 1 if previous is not None else 0, key)
    return result


class SettingsNavigation(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        # The native insertion line is centered on the first item's top edge.
        # Keep the entire stroke inside the viewport, including at index zero.
        self.setSpacing(4)
        self.setAccessibleName("설정 목록")
        self.setToolTip("드래그하거나 Alt+위/아래 방향키로 설정 순서를 변경합니다.")

    def dropEvent(self, event):
        if event.source() is not self:
            event.ignore()
            return
        super().dropEvent(event)

    def keyPressEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.AltModifier and event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            source = self.currentRow()
            target = source + (-1 if event.key() == Qt.Key.Key_Up else 1)
            if 0 <= source < self.count() and 0 <= target < self.count():
                root = self.model().index(-1, -1)
                self.model().moveRow(root, source, root, target + (target > source))
            event.accept()
            return
        super().keyPressEvent(event)
