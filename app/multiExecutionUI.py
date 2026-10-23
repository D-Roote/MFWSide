"""Fixed batch toolbar and independently managed instance cards."""
from copy import deepcopy

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from app.controls import PopupOnlyWheelComboBox
from app.logPresentation import format_log_message
from app.monitoring import normalize_connection_preferences, supported_presets, window_process_info
from app.multiExecution import SessionManager, STATE_LABELS, discover_connections
from app.sessionRuntime import target_identity
from app.adbWindow import match_windows, window_identity
from app.runtime import PROGRAM_LAUNCH_ENTRY


class _SingleLineLabel(QLabel):
    """Keep full accessible text, but never let long text enlarge a card."""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(rect, self.alignment(), text)
        painter.end()


class DiscoveryWorker(QThread):
    def __init__(self, snapshot, preferences, preset=None, parent=None):
        super().__init__(parent)
        self.snapshot, self.preferences, self.preset = deepcopy(snapshot), deepcopy(preferences), preset
        self.result = ([], [])
        self.emulators = []

    def run(self):
        try:
            self.result = discover_connections(self.snapshot, self.preferences, self.preset)
            if any(connection["target"]["kind"] == "Adb" for connection in self.result[0]):
                import os
                from maa.toolkit import Toolkit
                for window in Toolkit.find_desktop_windows() or []:
                    if not window.window_name:
                        continue
                    hwnd = getattr(window.hwnd, "value", window.hwnd)
                    pid, path = window_process_info(hwnd)
                    if pid and pid != os.getpid():
                        self.emulators.append({"hwnd": hwnd, "pid": pid, "path": path, "label": window.window_name})
                matches, ambiguous = match_windows([connection["target"] for connection in self.result[0]], self.emulators)
                for connection in self.result[0]:
                    key = connection["target"].get("key")
                    if key in matches:
                        connection["emulator"] = matches[key]
                    elif key in ambiguous:
                        connection["target"]["_window_ambiguous"] = True
        except Exception as error:
            self.result = ([], [str(error)])


class InstanceCard(QFrame):
    def __init__(self, panel, session):
        super().__init__(panel.cards_contents)
        self.panel, self.session_id = panel, session.id
        self.setObjectName("multiInstanceCard")
        self._syncing = True
        self.targets = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(6)
        rows = QGridLayout()
        rows.setHorizontalSpacing(6)
        rows.setVerticalSpacing(6)
        rows.setColumnStretch(0, 2)
        rows.setColumnStretch(1, 3)
        header = QHBoxLayout()
        header.setSpacing(rows.horizontalSpacing())
        self.title = _SingleLineLabel(session.title)
        self.title.setObjectName("settingsRowTitle")
        self.title.setToolTip(session.title)
        self.status = _SingleLineLabel()
        self.status.setObjectName("settingsSectionDescription")
        rows.addWidget(self.title, 0, 0)
        task_selection = QHBoxLayout()
        task_selection.setSpacing(6)
        self.task_preset_combo = PopupOnlyWheelComboBox()
        self.task_preset_combo.setObjectName("monitorComboBox")
        self.task_preset_combo.setAccessibleName("인스턴스 작업 프리셋")
        self.task_preset_combo.setMinimumWidth(0)
        self.task_preset_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        task_selection.addWidget(self.status, 1)
        task_selection.addWidget(self.task_preset_combo, 1)
        rows.addLayout(task_selection, 0, 1)
        self.adb_details_toggle = QPushButton("설정")
        self.adb_details_toggle.setObjectName("monitorActionButton")
        self.adb_details_toggle.setCheckable(True)
        self.adb_details_toggle.setAccessibleName("ADB 상세 연결 설정 펼치기")
        self.adb_details_toggle.setToolTip("ADB 경로·주소·편의 최소화 창 설정")
        self.start_button = QPushButton("실행")
        self.stop_button = QPushButton("중지")
        self.delete_button = QPushButton("삭제")
        for button in (self.start_button, self.stop_button, self.delete_button):
            button.setObjectName("monitorActionButton")
            header.addWidget(button)
        rows.addLayout(header, 0, 2)
        connection_row = QHBoxLayout()
        connection_row.setSpacing(rows.horizontalSpacing())
        connection_row.addStretch()
        self.preset_combo = PopupOnlyWheelComboBox()
        self.preset_combo.setAccessibleName("인스턴스 컨트롤러")
        self.preset_combo.setObjectName("monitorComboBox")
        for preset in panel.presets:
            self.preset_combo.addItem(preset.get("label") or preset["name"], preset["name"])
        self.target_combo = PopupOnlyWheelComboBox()
        self.target_combo.setAccessibleName("인스턴스 연결 대상")
        self.target_combo.setObjectName("monitorComboBox")
        self.target_combo.addItem("대상을 탐색하거나 ADB 주소를 입력하세요.", None)
        self.adb_path = QLineEdit()
        self.adb_path.setPlaceholderText("ADB 경로 (자동 탐색 또는 직접 입력)")
        self.adb_path.setAccessibleName("인스턴스 ADB 경로")
        self.adb_path.setObjectName("monitorLineEdit")
        self.address = QLineEdit()
        self.address.setPlaceholderText("ADB 주소 또는 기기 serial")
        self.address.setAccessibleName("인스턴스 ADB 주소")
        self.address.setObjectName("monitorLineEdit")
        self.emulator_combo = PopupOnlyWheelComboBox()
        self.emulator_combo.setObjectName("monitorComboBox")
        self.emulator_combo.setAccessibleName("ADB 편의 최소화 창")
        self.emulator_combo.setToolTip("ADB 앱플레이어 이름과 창 제목으로 자동 선택합니다. 필요한 경우 직접 변경하세요.")
        self.emulator_combo.addItem("최소화 대상 선택", {})
        for widget in (self.preset_combo, self.target_combo, self.adb_path, self.address, self.emulator_combo):
            widget.setMinimumWidth(0)
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        rows.addWidget(self.preset_combo, 1, 0)
        rows.addWidget(self.target_combo, 1, 1)
        self.discover_button = QPushButton("탐색")
        self.discover_button.setAccessibleName("인스턴스 대상 탐색")
        self.discover_button.setToolTip("실행 중인 연결 대상 탐색")
        self.check_button = QPushButton("확인")
        self.check_button.setAccessibleName("인스턴스 연결 확인")
        self.check_button.setToolTip("선택한 대상의 연결 확인")
        connection_row.addWidget(self.adb_details_toggle)
        for button in (self.discover_button, self.check_button):
            button.setObjectName("monitorActionButton")
            connection_row.addWidget(button)
        rows.addLayout(connection_row, 1, 2)
        root.addLayout(rows)
        self.adb_details = QWidget(self)
        details = QVBoxLayout(self.adb_details)
        details.setContentsMargins(0, 4, 0, 0)
        details.setSpacing(6)
        address_row = QHBoxLayout()
        address_row.setSpacing(6)
        address_row.addWidget(self.adb_path, 3)
        address_row.addWidget(self.address, 2)
        details.addLayout(address_row)
        details.addWidget(self.emulator_combo)
        root.addWidget(self.adb_details)
        self.adb_details_toggle.toggled.connect(self._toggle_adb_details)
        self.start_button.clicked.connect(lambda: panel.start_sessions([session.id]))
        self.stop_button.clicked.connect(lambda: panel.manager.stop(session.id))
        self.delete_button.clicked.connect(lambda: panel.manager.remove(session.id))
        self.discover_button.clicked.connect(lambda: panel.discover(session.id))
        self.check_button.clicked.connect(lambda: panel.start_sessions([session.id], mode="check"))
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        self.target_combo.currentIndexChanged.connect(self._target_changed)
        self.adb_path.editingFinished.connect(self._configure)
        self.address.editingFinished.connect(self._configure)
        self.emulator_combo.currentIndexChanged.connect(self._emulator_changed)
        self.task_preset_combo.currentIndexChanged.connect(self._task_preset_changed)
        self.set_connection(session.connection)
        self._syncing = False
        self.refresh()

    def set_connection(self, connection):
        self._syncing = True
        preset = connection.get("preset") or self.panel.preferred_preset()
        self.preset_combo.setCurrentIndex(self.preset_combo.findData(preset))
        target = connection.get("target")
        self.targets = [deepcopy(target)] if target else []
        self.target_combo.clear()
        self.target_combo.addItem("대상을 선택하세요.", None)
        if target:
            self.target_combo.addItem(target.get("label") or target.get("key"), target["key"])
            self.target_combo.setCurrentIndex(1)
        preferences = connection.get("preferences", self.panel.default_preferences())
        self.adb_path.setText(preferences.get("adb_path", ""))
        self.address.setText(preferences.get("address", ""))
        self.emulator_combo.setCurrentIndex(0)
        if connection.get("emulator"):
            self.emulator_combo.addItem(connection["emulator"].get("label", "선택한 창"), connection["emulator"])
            self.emulator_combo.setCurrentIndex(self.emulator_combo.count() - 1)
        self._syncing = False
        self._show_adb_fields()

    def _show_adb_fields(self):
        adb = any(p["name"] == self.preset_combo.currentData() and p["type"] == "Adb" for p in self.panel.presets)
        self.adb_details_toggle.setVisible(adb)
        if not adb:
            self.adb_details_toggle.setChecked(False)
        self.adb_details.setVisible(adb and self.adb_details_toggle.isChecked())

    def _toggle_adb_details(self, checked):
        self.adb_details_toggle.setAccessibleName("ADB 상세 연결 설정 접기" if checked else "ADB 상세 연결 설정 펼치기")
        self._show_adb_fields()

    def _preset_changed(self):
        if self._syncing:
            return
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        self.targets = []
        self.target_combo.addItem("대상을 선택하세요.", None)
        self.target_combo.blockSignals(False)
        self._show_adb_fields()
        self._configure()

    def current_target(self):
        # Native ADB method masks are uint64 (often > signed int64). QVariant
        # recursively converts dictionaries and overflows on those values.
        # Keep the original DTO in Python; Qt only stores the string identity.
        key = self.target_combo.currentData()
        return next((target for target in self.targets if target.get("key") == key), None)

    def _target_changed(self):
        target = self.current_target()
        if not self._syncing and target and target.get("kind") == "Adb":
            self.address.setText(target["key"])
            previous = self.panel.manager.sessions[self.session_id].connection
            if (not previous.get("emulator") or (previous.get("target") or {}).get("key") != target["key"]
                    or previous["emulator"].get("automatic")):
                self._auto_select_emulator(target)
        self._configure()

    def _auto_select_emulator(self, target):
        windows = [self.emulator_combo.itemData(index) for index in range(1, self.emulator_combo.count())]
        others = [session.connection for session in self.panel.manager.sessions.values() if session.id != self.session_id]
        reserved = [(connection.get("target") if (connection.get("target") or {}).get("kind") == "Win32"
                     else connection.get("emulator")) or {} for connection in others]
        targets = [*self.targets, *(connection.get("target") or {} for connection in others)]
        matches, ambiguous = match_windows(targets, windows, reserved=reserved)
        if target["key"] in ambiguous:
            target["_window_ambiguous"] = True
        window = {} if target.get("_window_ambiguous") else matches.get(target["key"], {})
        self.emulator_combo.blockSignals(True)
        selected = 0
        if window:
            selected = next((index for index in range(1, self.emulator_combo.count())
                             if window_identity(self.emulator_combo.itemData(index)) == window_identity(window)), 0)
            if selected:
                self.emulator_combo.setItemData(selected, window)
        self.emulator_combo.setCurrentIndex(selected)
        self.emulator_combo.blockSignals(False)

    def _emulator_changed(self):
        if not self._syncing:
            # Even a formerly auto-selected entry becomes an explicit override
            # when the user chooses it from the dropdown.
            window = self.emulator_combo.currentData()
            if isinstance(window, dict) and window.get("automatic"):
                window = dict(window)
                window.pop("automatic", None)
                self.emulator_combo.setItemData(self.emulator_combo.currentIndex(), window)
        self._configure()

    def _configure(self):
        if self._syncing:
            return
        target = self.current_target()
        preferences = {"adb_path": self.adb_path.text().strip(), "address": self.address.text().strip()}
        adb = self.adb_path.isVisible() or any(p["name"] == self.preset_combo.currentData() and p["type"] == "Adb"
                                             for p in self.panel.presets)
        if adb and preferences["address"]:
            if not target or target.get("key") != preferences["address"]:
                target = {"kind": "Adb", "key": preferences["address"], "label": preferences["address"]}
                if (self.emulator_combo.currentData() or {}).get("automatic"):
                    self.emulator_combo.blockSignals(True)
                    self.emulator_combo.setCurrentIndex(0)
                    self.emulator_combo.blockSignals(False)
        try:
            self.panel.manager.configure(self.session_id, {
                "preset": self.preset_combo.currentData(), "target": deepcopy(target), "preferences": preferences,
                "emulator": self.emulator_combo.currentData() or {},
            })
        except ValueError as error:
            self.panel.report_status(str(error))
            self.set_connection(self.panel.manager.sessions[self.session_id].connection)

    def refresh(self):
        session = self.panel.manager.sessions[self.session_id]
        emulator = session.connection.get("emulator") or {}
        if self.emulator_combo.currentData() != emulator:
            self.emulator_combo.blockSignals(True)
            index = (next((index for index in range(1, self.emulator_combo.count())
                           if window_identity(self.emulator_combo.itemData(index)) == window_identity(emulator)), -1)
                     if emulator else 0)
            if emulator and index < 0:
                self.emulator_combo.addItem(emulator.get("label", "자동 선택한 창"), emulator)
                index = self.emulator_combo.count() - 1
            if emulator:
                self.emulator_combo.setItemData(index, emulator)
            self.emulator_combo.setCurrentIndex(index)
            self.emulator_combo.blockSignals(False)
        self.sync_task_presets()
        state = STATE_LABELS.get(session.state, session.state)
        self.status.setText(state)
        details = [state]
        if session.result and session.state == "failed":
            details.append(session.result["message"])
        details.extend(list(session.logs)[-12:])
        self.status.setToolTip(format_log_message("\n".join(details),
                                                diagnostics=self.panel.window.diagnostic_logs_enabled))
        self.target_combo.setToolTip(self.target_combo.currentText())
        editable = (not session.active and not self.panel.window.isRunning and not self.panel.closing
                    and self.panel.discovery_worker is None and not self.panel.window._completion_pending
                    and self.panel.window.monitor.pending_start is None
                    and not self.panel.daily_active)
        for control in (self.preset_combo, self.target_combo, self.adb_path, self.address, self.emulator_combo,
                        self.task_preset_combo, self.adb_details_toggle,
                        self.discover_button, self.check_button, self.start_button):
            control.setEnabled(editable)
        connected = target_identity(session.connection) is not None
        preset_available = not session.task_preset or any(item["id"] == session.task_preset for item in self.panel.window.task_presets)
        self.start_button.setEnabled(editable and connected and preset_available)
        self.check_button.setEnabled(editable and connected)
        self.stop_button.setEnabled(session.active and session.state != "stopping")
        self.delete_button.setEnabled(not session.remove_pending)

    def sync_task_presets(self):
        session = self.panel.manager.sessions[self.session_id]
        choices = tuple((item["id"], item["name"]) for item in self.panel.window.task_presets)
        fingerprint = (choices, session.task_preset)
        if (getattr(self, "_task_preset_choices", None) == fingerprint
                and self.task_preset_combo.currentData() == session.task_preset):
            return
        self._task_preset_choices = fingerprint
        combo = self.task_preset_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem("현재 작업 목록", "")
        for preset_id, name in choices:
            combo.addItem(name, preset_id)
        index = combo.findData(session.task_preset)
        if index < 0:
            combo.addItem("사용할 수 없는 프리셋", session.task_preset)
            index = combo.count() - 1
        combo.setCurrentIndex(index)
        combo.setToolTip(combo.currentText())
        combo.blockSignals(False)

    def _task_preset_changed(self):
        if self._syncing:
            return
        try:
            self.panel.manager.set_task_preset(self.session_id, self.task_preset_combo.currentData() or "")
        except ValueError as error:
            self.panel.report_status(str(error))
            self.sync_task_presets()


class MultiExecutionPanel(QWidget):
    shutdown_ready = Signal()

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window = window
        self.setObjectName("multiExecutionPanel")
        self.presets = supported_presets(window.runtime.interface, window.runtime.resource_config)
        self.manager = SessionManager(self)
        self.cards = {}
        self.discovery_worker = None
        self.closing = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 6, 0)
        root.setSpacing(14)
        toolbar = QFrame(self)
        toolbar.setObjectName("multiToolbar")
        tools = QVBoxLayout(toolbar)
        tools.setContentsMargins(16, 14, 16, 14)
        title = QLabel("다중 실행")
        title.setObjectName("settingsSectionTitle")
        tools.addWidget(title)
        description = QLabel("현재 작업 목록 또는 선택한 프리셋으로 실행합니다. 연결 대상은 인스턴스별로 관리하며 나머지 사용자 설정은 공유합니다.")
        description.setObjectName("settingsSectionDescription")
        description.setWordWrap(True)
        tools.addWidget(description)
        buttons = QHBoxLayout()
        self.start_all = QPushButton("일괄 실행")
        self.stop_all = QPushButton("일괄 중지")
        self.delete_all = QPushButton("인스턴스 일괄 삭제")
        self.add_button = QPushButton("인스턴스 추가")
        self.auto_button = QPushButton("자동 인식 / 연결")
        for button in (self.start_all, self.stop_all, self.delete_all, self.add_button, self.auto_button):
            button.setObjectName("monitorActionButton")
            buttons.addWidget(button)
        tools.addLayout(buttons)
        root.addWidget(toolbar)
        self.cards_scroll = QScrollArea(self)
        self.cards_scroll.setObjectName("extraFeatureScroll")
        self.cards_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards_contents = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_contents)
        self.cards_layout.setContentsMargins(0, 0, 6, 0)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()
        self.cards_scroll.setWidget(self.cards_contents)
        root.addWidget(self.cards_scroll, 1)
        self.manager.changed.connect(self._changed)
        self.manager.log.connect(self._log)
        self.manager.activity_changed.connect(self.refresh)
        self.start_all.clicked.connect(lambda: self.start_sessions(list(self.manager.sessions)))
        self.stop_all.clicked.connect(self.manager.stop_all)
        self.delete_all.clicked.connect(self._delete_all)
        self.add_button.clicked.connect(lambda: self.manager.add({"preset": self.preferred_preset()}))
        self.auto_button.clicked.connect(lambda: self.discover())

    def preferred_preset(self):
        selected = self.window.settings_panel.controller_settings().get("name")
        return selected if any(p["name"] == selected for p in self.presets) else (self.presets[0]["name"] if self.presets else "")

    def default_preferences(self):
        return normalize_connection_preferences(self.window.settings_panel.config.get("connection"))

    def snapshot_for_session(self, session_id, base=None):
        session = self.manager.sessions[session_id]
        request = deepcopy(base if base is not None else self.window.multi_execution_snapshot(include_tasks=False))
        request["queue"] = (self.window.build_task_preset_queue(session.task_preset) if session.task_preset
                            else self.window.build_execution_queue())
        request["after_actions"] = (deepcopy(self.window.after_actions.current)
                                    if any(entry != PROGRAM_LAUNCH_ENTRY for entry, _ in request["queue"]) else {})
        if not request["queue"]:
            raise ValueError("실행할 작업이 선택되지 않았습니다. 현재 작업 목록 또는 프리셋을 확인하세요.")
        return request

    def refresh_task_presets(self):
        for card in self.cards.values():
            card.refresh()

    @property
    def daily_active(self):
        daily = getattr(self.window, "daily_restart", None)
        return daily is not None and daily.active

    def report_status(self, message):
        message = format_log_message(message, diagnostics=self.window.diagnostic_logs_enabled)
        if message.strip():
            self.window.append_log(f"[다중 실행] {message}")

    def _changed(self, session_id):
        if session_id not in self.manager.sessions:
            card = self.cards.pop(session_id, None)
            if card is not None:
                card.hide()
                self.cards_layout.removeWidget(card)
                card.deleteLater()
        elif session_id not in self.cards:
            card = InstanceCard(self, self.manager.sessions[session_id])
            self.cards[session_id] = card
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        else:
            self.cards[session_id].refresh()
        self.window._schedule_ui_state_save()
        self.refresh()

    def _log(self, session_id, text):
        session = self.manager.sessions.get(session_id)
        text = format_log_message(text, diagnostics=self.window.diagnostic_logs_enabled)
        if session and text.strip():
            self.window.append_log(f"[{session.title}] {text}")

    def _delete_all(self):
        for session_id in list(self.manager.sessions):
            self.manager.remove(session_id)

    def start_sessions(self, ids, mode="run", snapshot=None, *, plans=None):
        if self.window.settings_panel.program_test_busy or self.window.settings_panel.management.busy or self.window.app_startup.active or self.daily_active:
            self.report_status("프로그램 실행 테스트가 끝난 뒤 다시 시도하세요.")
            return
        if self.window.isRunning or self.closing or self.window._completion_pending or self.discovery_worker is not None:
            self.report_status("기본 작업이나 대상 탐색이 끝난 뒤 다시 시도하세요.")
            return
        try:
            snapshot_given = snapshot is not None
            snapshot = deepcopy(snapshot if snapshot_given else self.window.multi_execution_snapshot(include_tasks=False))
        except (KeyError, ValueError, TypeError) as error:
            self.report_status(f"작업 구성 또는 프리셋을 확인하세요: {error}")
            return
        valid = []
        requests = deepcopy(plans) if plans is not None else {}
        for session_id in ids:
            session = self.manager.sessions.get(session_id)
            if session is None or session.active:
                continue
            try:
                if session_id not in requests:
                    requests[session_id] = (deepcopy(snapshot) if snapshot_given or mode != "run"
                                            else self.snapshot_for_session(session_id, snapshot))
                requests[session_id]["reserved_windows"] = [
                    deepcopy((other.connection.get("target") if (other.connection.get("target") or {}).get("kind") == "Win32"
                              else other.connection.get("emulator")) or {})
                    for other in self.manager.sessions.values() if other.id != session_id
                ]
                self.manager.validate_start(session_id, requests[session_id])
                valid.append(session_id)
            except ValueError as error:
                self.report_status(str(error))
                return  # A partial batch must never trigger global completion actions.
        if not valid:
            return
        if self.window.monitor.prepare_task_start(lambda: self.start_sessions(valid, mode, snapshot, plans=requests)):
            return
        if mode == "run" and snapshot.get("clear_log") and not self.manager.active:
            self.window.clear_log()
        started = False
        for session_id in valid:
            try:
                if self.manager.start(session_id, requests[session_id], mode):
                    started = True
            except Exception as error:
                self.report_status(str(error))
                self.manager.mark_wave_failure(session_id, error)
                break
        if started and mode == "run":
            self.window._minimize_app_after_task_start()

    def discover(self, session_id=None):
        if self.window.settings_panel.program_test_busy or self.window.settings_panel.management.busy or self.window.app_startup.active or self.daily_active:
            return
        if self.discovery_worker is not None or self.window.isRunning or self.closing or self.window._completion_pending:
            return
        if session_id is None and self.manager.active:
            self.report_status("인스턴스 실행 또는 연결 확인이 끝난 뒤 자동 인식을 다시 시도하세요.")
            return
        snapshot = self.window.multi_execution_snapshot(include_tasks=False)
        card = self.cards.get(session_id)
        preferences = ({"adb_path": card.adb_path.text(), "address": card.address.text()} if card else self.default_preferences())
        preset = card.preset_combo.currentData() if card else None
        self.report_status("interface.json의 실행 중인 대상들을 탐색하고 있습니다…")
        self.discovery_worker = worker = DiscoveryWorker(snapshot, preferences, preset, self)
        worker.finished.connect(lambda: self._discovered(worker, session_id, snapshot))
        worker.start()
        self.refresh()

    def _discovered(self, worker, session_id, snapshot):
        found, errors = worker.result
        self.discovery_worker = None
        worker.deleteLater()
        if self.closing:
            self.refresh()
            return
        if session_id is not None and session_id not in self.cards:
            self.report_status("삭제된 인스턴스의 탐색 결과를 무시했습니다.")
            self.refresh()
            return
        message = f"대상 {len(found)}개 확인." + (" " + " / ".join(errors) if errors else "")
        if session_id is not None:
            card = self.cards[session_id]
            current = self.manager.sessions[session_id].connection
            current_identity = target_identity(current)
            occupied = {target_identity(session.connection) for session in self.manager.sessions.values()
                        if session.id != session_id}
            available = [connection for connection in found if target_identity(connection) not in occupied]
            if not available:
                # No search result must erase an existing device's SDK metadata,
                # or turn a stale default address into a duplicate connection.
                card.set_connection(current)
                if found:
                    message += " 다른 인스턴스에서 사용 중이므로 사용 가능한 대상이 없습니다."
                self.report_status(message)
                self.refresh()
                return
            previous_emulator = card.emulator_combo.currentData() or {}
            card.target_combo.blockSignals(True)
            card.target_combo.clear()
            card.target_combo.addItem("대상을 선택하세요.", None)
            card.targets = [deepcopy(connection["target"]) for connection in available]
            for target in card.targets:
                card.target_combo.addItem(target["label"], target["key"])
            card.target_combo.blockSignals(False)
            card.emulator_combo.blockSignals(True)
            card.emulator_combo.clear()
            card.emulator_combo.addItem("최소화 대상 선택", {})
            for emulator in worker.emulators:
                card.emulator_combo.addItem(emulator["label"], emulator)
                if emulator == previous_emulator:
                    card.emulator_combo.setCurrentIndex(card.emulator_combo.count() - 1)
            card.emulator_combo.blockSignals(False)
            selected = next((index for index, connection in enumerate(available, 1)
                             if target_identity(connection) == current_identity), 1)
            card.target_combo.setCurrentIndex(selected)
            if len(available) != len(found):
                message += f" 사용 가능한 대상 {len(available)}개."
        else:
            if self.manager.active or self.window.isRunning or self.window._completion_pending:
                self.report_status("작업이 시작되어 자동 인식 결과를 적용하지 않았습니다. 작업 종료 후 다시 시도하세요.")
                self.refresh()
                return
            # Global discovery replaces the idle list; per-card discovery stays local.
            self._delete_all()
            ids = [session_id for connection in found if (session_id := self.manager.add(connection)) is not None]
            for instance_id in ids:
                card = self.cards[instance_id]
                for emulator in worker.emulators:
                    if not any(window_identity(card.emulator_combo.itemData(index)) == window_identity(emulator)
                               for index in range(1, card.emulator_combo.count())):
                        card.emulator_combo.addItem(emulator["label"], emulator)
            if ids:
                self.start_sessions(ids, "check", snapshot)
        self.report_status(message)
        self.refresh()

    @property
    def ready_to_close(self):
        return not self.manager.active and self.discovery_worker is None

    def refresh(self):
        busy = self.window.isRunning or self.closing or self.window._completion_pending or self.discovery_worker is not None
        busy = busy or self.window.settings_panel.program_test_busy or self.window.settings_panel.management.busy or self.window.app_startup.active or self.daily_active
        self.start_all.setEnabled(not busy and bool(self.manager.sessions))
        self.stop_all.setEnabled(self.manager.active)
        self.add_button.setEnabled(not busy)
        self.auto_button.setEnabled(not busy and not self.manager.active)
        self.delete_all.setEnabled(bool(self.manager.sessions))
        for card in self.cards.values():
            card.refresh()
        if hasattr(self.window, "option_list_widget"):
            self.window.check_start_button_state()
            self.window.monitor._sync()
        if self.closing and self.ready_to_close:
            self.shutdown_ready.emit()

    def shutdown(self):
        self.closing = True
        self.manager.shutdown()
        self.refresh()
