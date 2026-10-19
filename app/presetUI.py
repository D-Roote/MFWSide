"""Compact task-preset controls, separate from execution and persistence."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget

from app.taskPresets import MAX_PRESET_NAME


class PresetList(QListWidget):
    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.count():
            painter = QPainter(self.viewport())
            painter.setPen(self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text))
            painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                             "저장한 프리셋이 없습니다.")
            painter.end()


class TaskPresetPanel(QWidget):
    save_requested = Signal(str)
    replace_requested = Signal(str, str)
    apply_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("taskPresetPanel")
        self.editable = True
        self._presets = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 4, 8)
        layout.setSpacing(8)
        description = QLabel("작업 순서·선택·세부 옵션을 저장합니다.")
        description.setObjectName("presetDescription")
        description.setWordWrap(True)
        layout.addWidget(description)
        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        self.name_input = QLineEdit()
        self.name_input.setObjectName("presetNameInput")
        self.name_input.setPlaceholderText("프리셋 이름")
        self.name_input.setAccessibleName("프리셋 이름")
        self.name_input.setMaxLength(MAX_PRESET_NAME)
        self.name_input.setMinimumWidth(0)
        self.save_button = QPushButton("새로 저장")
        self.save_button.setObjectName("presetActionButton")
        name_row.addWidget(self.name_input, 1)
        name_row.addWidget(self.save_button)
        layout.addLayout(name_row)
        self.list = PresetList()
        self.list.setObjectName("taskPresetList")
        self.list.setAccessibleName("저장된 작업 프리셋")
        self.list.setMinimumWidth(0)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        layout.addWidget(self.list, 1)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.apply_button = QPushButton("적용")
        self.replace_button = QPushButton("덮어쓰기")
        self.delete_button = QPushButton("삭제")
        for button in (self.apply_button, self.replace_button, self.delete_button):
            button.setObjectName("presetActionButton")
            actions.addWidget(button, 1)
        self.apply_button.setObjectName("presetApplyButton")
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setObjectName("presetStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)
        self.list.currentItemChanged.connect(self._selection_changed)
        self.save_button.clicked.connect(lambda: self.save_requested.emit(self.name_input.text()))
        self.name_input.returnPressed.connect(self.save_button.click)
        self.apply_button.clicked.connect(lambda: self.apply_requested.emit(self.selected_id()))
        self.replace_button.clicked.connect(lambda: self.replace_requested.emit(self.selected_id(), self.name_input.text()))
        self.delete_button.clicked.connect(lambda: self.delete_requested.emit(self.selected_id()))
        self._refresh_buttons()

    def selected_id(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else ""

    def show_presets(self, presets, selected_id=""):
        self._presets = {preset["id"]: preset for preset in presets}
        self.list.blockSignals(True)
        self.list.clear()
        selected_row = 0
        for row, preset in enumerate(presets):
            item = QListWidgetItem(preset["name"])
            item.setData(Qt.ItemDataRole.UserRole, preset["id"])
            item.setToolTip(f"{preset['name']}\n작업 {len(preset['tasks'])}개")
            self.list.addItem(item)
            if preset["id"] == selected_id:
                selected_row = row
        self.list.blockSignals(False)
        self.list.setCurrentRow(selected_row if presets else -1)
        self._selection_changed()

    def _selection_changed(self, *_args):
        preset = self._presets.get(self.selected_id())
        self.name_input.setText(preset["name"] if preset else "")
        self._refresh_buttons()

    def set_editable(self, editable):
        self.editable = bool(editable)
        self._refresh_buttons()

    def _refresh_buttons(self):
        self.name_input.setEnabled(self.editable)
        self.save_button.setEnabled(self.editable)
        for button in (self.apply_button, self.replace_button, self.delete_button):
            button.setEnabled(self.editable and bool(self.selected_id()))

    def show_status(self, message, *, error=False):
        self.status.setProperty("error", bool(error))
        self.status.setText(message)
        self.status.setVisible(bool(message))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
