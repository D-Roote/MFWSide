"""Application-modal startup controls with a lightweight painted countdown."""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class CountdownDial(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.maximum = 1
        self.value = 0
        self.setFixedSize(184, 184)
        self.value_label = QLabel("0", self)
        self.value_label.setObjectName("startupCountdownValue")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_label.setGeometry(10, 47, 164, 90)
        self.unit_label = QLabel("초", self)
        self.unit_label.setObjectName("startupCountdownUnit")
        self.unit_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.unit_label.setGeometry(0, 138, 184, 24)
        self.setAccessibleName("자동 시작까지 남은 초")

    def set_value(self, value, maximum):
        self.value_label.setText(str(value))
        self.unit_label.setText("초")
        if (self.value, self.maximum) != (value, maximum):
            self.value, self.maximum = value, maximum
            self.update()

    def set_pending(self, text, unit):
        self.set_value(0, 1)
        self.value_label.setText(text)
        self.unit_label.setText(unit)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(5, 5, 174, 174)
        accent = QColor("#00AEEF")
        track = QColor(accent)
        track.setAlpha(40)
        pen = QPen(track, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawEllipse(rect)
        if self.value:
            pen.setColor(accent)
            pen.setWidthF(3)
            painter.setPen(pen)
            painter.drawArc(rect, 90 * 16, -round(360 * 16 * min(1, self.value / self.maximum)))
        painter.end()


class AppStartupDialog(QDialog):
    start_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("startupDialog")
        self.setWindowTitle("자동 시작")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(420)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("startupDialogCard")
        root.addWidget(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(32, 26, 32, 26)
        layout.setSpacing(12)
        self.title_label = QLabel("자동 시작")
        self.title_label.setObjectName("startupDialogTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)
        self.description = QLabel()
        self.description.setObjectName("startupDialogDescription")
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        self.description.setWordWrap(True)
        self.description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.description.setMinimumHeight(38)
        layout.addWidget(self.description)
        countdown = QHBoxLayout()
        countdown.setSpacing(0)
        self.countdown = CountdownDial()
        countdown.addStretch(1)
        countdown.addWidget(self.countdown)
        countdown.addStretch(1)
        layout.addLayout(countdown)
        layout.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.cancel_button = QPushButton("자동 시작 취소")
        self.cancel_button.setObjectName("startupCancelButton")
        self.cancel_button.setDefault(True)
        self.start_button = QPushButton("지금 시작")
        self.start_button.setObjectName("startupNowButton")
        self.start_button.setAutoDefault(False)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.start_button)
        layout.addLayout(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.start_button.clicked.connect(self.start_requested)
        self.setAccessibleName("자동 시작 안내 및 제어")

    def showEvent(self, event):
        super().showEvent(event)
        parent = self.parentWidget()
        bounds = parent.screen().availableGeometry()
        rect = self.frameGeometry()
        rect.moveCenter(parent.frameGeometry().center())
        self.move(max(bounds.left(), min(rect.left(), bounds.right() - rect.width() + 1)),
                  max(bounds.top(), min(rect.top(), bounds.bottom() - rect.height() + 1)))

    def set_remaining(self, seconds, *, total_seconds=None, preparing=False):
        self.title_label.setText("프로그램 준비 대기" if preparing else "작업 자동 시작")
        self.description.setText("대상 프로그램의 로딩이 끝날 때까지 기다립니다." if preparing
                                 else "남은 시간이 지나면 선택한 작업을 자동으로 시작합니다.")
        self.countdown.set_value(seconds, max(1, seconds if total_seconds is None else total_seconds))
        self.start_button.setEnabled(not preparing)
        self.start_button.setToolTip("프로그램 준비가 끝나면 사용할 수 있습니다." if preparing else "남은 대기를 생략하고 작업을 시작합니다.")
        self.setAccessibleDescription(f"{'프로그램 준비' if preparing else '작업 시작'}까지 {seconds}초")

    def set_launching(self):
        self.title_label.setText("대상 프로그램 실행")
        self.description.setText("시작 설정에 따라 대상 프로그램을 실행하고 있습니다.")
        self.start_button.setEnabled(False)
        self.countdown.set_pending("준비", "실행 중")

    def set_cancelling(self):
        self.title_label.setText("자동 시작 취소 중")
        self.description.setText("실행 요청이 정리될 때까지 잠시 기다려 주세요.")
        self.cancel_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.countdown.set_pending("취소", "정리 중")

    def reject(self):
        # ESC/Alt+F4 must cancel orchestration, not leave a hidden modal startup
        # running. The coordinator hides the dialog when its worker is finished.
        self.cancel_requested.emit()

    def closeEvent(self, event):
        event.ignore()
        self.reject()
