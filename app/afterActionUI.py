"""Completion settings hosted in the existing central detail pane."""
from PySide6.QtCore import QPointF, Qt, QThread, Signal
from PySide6.QtGui import QFontMetricsF, QPainter, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QLabel, QPushButton, QStyle, QStyleOptionButton,
    QVBoxLayout, QWidget, QSizePolicy,
)

from app.afterActions import SYSTEM_ACTIONS
from app.adbAppStop import AdbAppStopBackend
from app.identity import DEFAULT_APP_NAME, display_name


class OpticalCheckBox(QCheckBox):
    """Keep native interaction, but center visible glyph ink rather than line metrics."""
    def paintEvent(self, _event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        style = self.style()
        indicator = style.subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        text_rect = style.subElementRect(QStyle.SubElement.SE_CheckBoxContents, option, self)
        ink = QFontMetricsF(self.font(), self).tightBoundingRect(self.text())
        # QRect's inclusive bottom differs from a floating rectangle by half a
        # pixel. Anchor the baseline to the actual painted indicator center.
        baseline = indicator.y() + indicator.height() / 2.0 - ink.center().y()
        painter = QPainter(self)
        option.text = ""
        style.drawControl(QStyle.ControlElement.CE_CheckBox, option, painter, self)
        painter.setFont(self.font())
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(option.palette.color(group, QPalette.ColorRole.WindowText))
        painter.drawText(QPointF(text_rect.x(), baseline), self.text())
        painter.end()


class AfterActionPanel(QWidget):
    changed = Signal()

    def __init__(self, preferences, parent=None, controller_type="Win32", app_title=DEFAULT_APP_NAME):
        super().__init__(parent)
        self.preferences = preferences
        self.controller_type = controller_type
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._title(root, "작업 완료 후 동작", first=True)
        info = QLabel("모든 작업이 정상 완료된 후 실행합니다.\n수동 중지·실패 시에는 실행하지 않습니다.")
        info.setObjectName("afterActionHint")
        info.setWordWrap(True)
        root.addWidget(info)
        self.once_checkbox = OpticalCheckBox("이번에만")
        self.once_checkbox.setObjectName("afterActionChoice")
        self.once_checkbox.setMinimumHeight(28)
        self.once_checkbox.setToolTip("이후 변경은 다음 정상 완료에만 적용합니다. 재실행 시 기존 영구 설정으로 돌아옵니다.")
        root.addWidget(self.once_checkbox)
        self.controls = {}
        self._title(root, "프로그램")
        for key, label in (("close_program", "대상 프로그램 종료"), ("close_app", "대상 앱 종료"),
                           ("close_emulator", "앱플레이어 종료 (미지원)"), ("close_maa", "")):
            control = self._checkbox(root, key, label)
            control.toggled.connect(lambda checked, name=key: self._update(name, checked))
        self.controls["close_program"].setToolTip("실제 작업 실행 창에 정상 종료를 요청합니다. 강제 종료하지 않습니다.")
        self.controls["close_emulator"].setEnabled(False)
        self.controls["close_emulator"].setToolTip("공통 ADB 명령만으로 앱플레이어 자체를 안전하게 종료할 수 없어 지원하지 않습니다.")
        self.adb_notice = QLabel("대상 앱 패키지 정보가 없어 선택한 기기의 사용자 설치 앱을 종료합니다.\n시스템·홈·입력기 앱은 제외하며, 앱플레이어 자체는 종료하지 않습니다.")
        self.adb_notice.setObjectName("afterActionHint")
        self.adb_notice.setWordWrap(True)
        root.addWidget(self.adb_notice)
        self._title(root, "시스템 · 하나만 선택")
        for key, label in SYSTEM_ACTIONS.items():
            control = self._checkbox(root, key, label)
            control.toggled.connect(lambda checked, name=key: self._update("system_action", name if checked else ""))
        warning = QLabel("절전·최대 절전은 PC 지원과 전원 권한이 필요합니다.\n시스템 종료 전 저장하지 않은 작업을 확인하세요.")
        warning.setObjectName("afterActionHint")
        warning.setWordWrap(True)
        root.addWidget(warning)
        self.clear_button = QPushButton("모두 해제")
        self.clear_button.setObjectName("monitorActionButton")
        self.clear_button.clicked.connect(self._clear)
        root.addWidget(self.clear_button)
        self.once_checkbox.toggled.connect(self._set_once)
        self.set_app_title(app_title)
        self.sync()

    def set_app_title(self, title):
        text = f"{display_name(title)} 종료"
        control = self.controls["close_maa"]
        control.setText(text)
        control.setToolTip(text)
        control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def _title(self, layout, text, first=False):
        if not first:
            separator = QFrame(self)
            separator.setObjectName("optionGroupSeparator")
            separator.setFixedHeight(1)
            layout.addWidget(separator)
        label = QLabel(text, self)
        label.setObjectName("optionGroupTitle")
        label.setProperty("firstGroup", first)
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label.setMinimumHeight(24)
        layout.addWidget(label)

    def _checkbox(self, layout, key, label):
        checkbox = OpticalCheckBox(label, self)
        checkbox.setObjectName("afterActionChoice")
        checkbox.setMinimumHeight(28)
        self.controls[key] = checkbox
        layout.addWidget(checkbox)
        return checkbox

    def sync(self):
        is_adb = self.controller_type == "Adb"
        self.controls["close_program"].setVisible(not is_adb)
        for key in ("close_app", "close_emulator"):
            self.controls[key].setVisible(is_adb)
        self.adb_notice.setVisible(is_adb)
        self.controls["close_app"].setEnabled(True)
        self.controls["close_app"].setToolTip(
            "작업에 사용한 ADB 기기의 사용자 설치 앱을 모두 종료합니다. 시스템·홈·입력기는 제외하고 앱 데이터는 삭제하지 않습니다. 앱을 다시 열기 전까지 백그라운드 알림도 중지될 수 있습니다."
        )
        values = [(self.once_checkbox, self.preferences.once)]
        for key, control in self.controls.items():
            checked = (self.preferences.current["system_action"] == key if key in SYSTEM_ACTIONS
                       else False if key == "close_emulator" else self.preferences.current[key])
            values.append((control, checked))
        for control, checked in values:
            old = control.blockSignals(True)
            control.setChecked(checked)
            control.blockSignals(old)

    def set_controller_type(self, controller_type):
        self.controller_type = controller_type
        self.sync()

    def _update(self, key, value):
        self.preferences.update(key, value)
        self.sync()
        self.changed.emit()

    def _set_once(self, checked):
        self.preferences.set_once(checked)
        self.changed.emit()

    def _clear(self):
        self.preferences.clear()
        self.sync()
        self.changed.emit()


class AdbAppStopWorker(QThread):
    """Wait off the GUI thread; no Maa controller survives in this worker."""
    log = Signal(str)

    def __init__(self, targets, parent=None):
        super().__init__(parent)
        self.targets = tuple(targets)

    def run(self):
        for target in self.targets:
            if self.isInterruptionRequested():
                break
            try:
                message = AdbAppStopBackend(self.isInterruptionRequested).stop(target)
                self.log.emit(f"{target.serial}: {message}")
            except Exception as error:
                self.log.emit(f"{target.serial}: 완료 후 대상 앱 종료 실패: {error}")
