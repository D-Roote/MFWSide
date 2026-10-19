from copy import deepcopy
import json
from pathlib import Path

from PySide6.QtCore import QEvent, QEasingCurve, QPropertyAnimation, Qt, QTime, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.runtime import WIN32_METHOD_DEFAULTS, WIN32_METHOD_PRIORITY
from app.controls import ClampedSecondsSpinBox, NoWheelSpinBox, NoWheelTimeEdit, PopupOnlyWheelComboBox
from app.programTest import ProgramTestWorker
from app.settingsNavigation import SETTINGS_SECTIONS, SettingsNavigation, normalize_section_order
from app.managementUI import ManagementPanel
from app.paths import PATHS
from app.identity import DEFAULT_APP_NAME, display_name
from app.startupSettings import WindowsStartupRegistration, normalize_startup_config
from app.monitoring import normalize_connection_preferences, normalize_screen_preferences
from app.pg_init import (
    DEFAULT_PROGRAM_CONFIG,
    find_auto_program_executable,
    find_program_executable,
    normalize_program_config,
    parse_extra_arguments,
    parse_manual_program_command,
    resolve_manual_program_path,
)

WIN32_MANUAL_PATH_DESCRIPTION = "비워 두면 자동 검색 결과를 사용합니다. 폴더 또는 실행 파일을 지정할 수 있습니다."
STARTUP_TIME_INPUT_WIDTH = 120
ADB_MANUAL_PATH_DESCRIPTION = (
    '실행 디바이스의 바로가기 생성 후, 우클릭-속성-바로 가기 탭 의 "대상"을 복사하여 사용합니다.'
)


DEFAULT_MAA_CONFIG = {
    "general": {
        "minimize_enabled": False,
        "minimize_app_on_task_start": False,
        "program_launch_task_enabled": True,
        "allow_option_edits_while_running": False,
        "clear_log_on_start": True,
    },
    "controller": {},
    "connection": {"adb_path": "", "address": ""},
    "monitor": {"mode": "single", "fps": 2},
    "program": DEFAULT_PROGRAM_CONFIG,
    "appearance": {
        "theme": "light",
    },
    "section_order": normalize_section_order(None),
    "startup": normalize_startup_config(None),
}


class SettingsStore:

    def __init__(self, config_path, legacy_user_config_path=None):
        self.config_path = Path(config_path)
        self.legacy_user_config_path = (
            Path(legacy_user_config_path) if legacy_user_config_path else None
        )

    @staticmethod
    def _read_json(path):
        if path is None or not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _write_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=4)
            file.flush()
        temp_path.replace(path)

    def load(self, default_controller=None):
        raw_config = self._read_json(self.config_path)
        config = deepcopy(DEFAULT_MAA_CONFIG)

        if raw_config is not None:
            raw_general = raw_config.get("general")
            if isinstance(raw_general, dict):
                for key in (
                    "minimize_enabled",
                    "minimize_app_on_task_start",
                    "program_launch_task_enabled",
                    "allow_option_edits_while_running",
                    "clear_log_on_start",
                ):
                    if isinstance(raw_general.get(key), bool):
                        config["general"][key] = raw_general[key]
                # 직전 개발 버전에서 저장한 부정형 설정을 긍정형으로 이관한다.
                if (
                    not isinstance(raw_general.get("program_launch_task_enabled"), bool)
                    and isinstance(raw_general.get("remove_program_launch_task"), bool)
                ):
                    config["general"]["program_launch_task_enabled"] = not raw_general[
                        "remove_program_launch_task"
                    ]

            raw_controller = raw_config.get("controller")
            if isinstance(raw_controller, dict):
                config["controller"] = deepcopy(raw_controller)

            config["program"] = normalize_program_config(raw_config.get("program"))
            config["connection"] = normalize_connection_preferences(raw_config.get("connection"))
            config["monitor"] = normalize_screen_preferences(raw_config.get("monitor"))
            config["section_order"] = normalize_section_order(raw_config.get("section_order"))
            config["startup"] = normalize_startup_config(raw_config.get("startup"))

            raw_appearance = raw_config.get("appearance")
            if isinstance(raw_appearance, dict):
                theme = raw_appearance.get("theme")
                if theme in {"system", "light", "dark"}:
                    config["appearance"]["theme"] = theme

        if not config["controller"] and isinstance(default_controller, dict):
            config["controller"] = self.serialize_controller(default_controller)

        legacy_config = self._read_json(self.legacy_user_config_path)
        migrated_legacy = None
        if legacy_config is not None and "minimize_enabled" in legacy_config:
            raw_general = raw_config.get("general") if raw_config else None
            has_new_value = isinstance(raw_general, dict) and isinstance(
                raw_general.get("minimize_enabled"), bool
            )
            legacy_value = legacy_config.get("minimize_enabled")
            if not has_new_value and isinstance(legacy_value, bool):
                config["general"]["minimize_enabled"] = legacy_value

            migrated_legacy = deepcopy(legacy_config)
            migrated_legacy.pop("minimize_enabled", None)

        self.save(config)
        if migrated_legacy is not None:
            self._write_json(self.legacy_user_config_path, migrated_legacy)
        return config

    def save(self, config):
        self._write_json(self.config_path, config)

    @staticmethod
    def serialize_controller(controller):
        if not isinstance(controller, dict):
            return {}
        return {
            key: deepcopy(controller[key])
            for key in ("name", "type", "win32", "adb")
            if key in controller
        }


class AssociatedControlLabel(QLabel):
    """A label that extends a checkable control's mouse hit area."""

    activated = Signal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.activated.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SettingsPanel(QWidget):
    SECTION_TOP_INSET = 4

    minimize_changed = Signal(bool)
    program_launch_task_enabled_changed = Signal(bool)
    runtime_option_editing_changed = Signal(bool)
    theme_changed = Signal(str)
    controller_changed = Signal(object)
    program_changed = Signal(object)
    program_test_busy_changed = Signal(bool)
    program_test_shutdown_ready = Signal()
    startup_changed = Signal()

    def __init__(
        self,
        config_path,
        legacy_user_config_path,
        controllers,
        supported_controller_names=None,
        parent=None,
        app_title=DEFAULT_APP_NAME,
    ):
        super().__init__(parent)
        self.app_title = display_name(app_title)
        self.setObjectName("settingsPanel")
        self._syncing_navigation = False
        self._sections = []
        self._program_test_worker = None
        self._program_test_closing = False
        self._program_test_allowed = True
        self._controls_connected = False
        self.program_test_guard = lambda: True
        self.startup_registration = WindowsStartupRegistration(PATHS.root_dir)
        self.controllers = self._filter_controllers(
            controllers, supported_controller_names
        )
        self.store = SettingsStore(config_path, legacy_user_config_path)
        default_controller = self.controllers[0] if self.controllers else None
        self.config = self.store.load(default_controller)

        self._build_ui()
        self.set_app_title(self.app_title)
        self._apply_config()
        self._connect_controls()
        self._controls_connected = True
        if self.startup_settings() != self.config["startup"]:
            self._save()
        QTimer.singleShot(0, lambda: self._sync_navigation_to_scroll(0))

    @staticmethod
    def _filter_controllers(controllers, supported_controller_names):
        if not isinstance(controllers, list):
            return []
        supported = (
            set(supported_controller_names)
            if isinstance(supported_controller_names, list)
            else None
        )
        result = []
        for controller in controllers:
            if not isinstance(controller, dict) or controller.get("type") not in ("Win32", "Adb"):
                continue
            name = controller.get("name")
            if not isinstance(name, str) or not name:
                continue
            if controller["type"] == "Win32" and not isinstance(controller.get("win32"), dict):
                continue
            if supported is not None and name not in supported:
                continue
            result.append(controller)
        return result

    def _build_ui(self):
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(16, 16, 6, 16)
        root_layout.setSpacing(16)

        self.navigation = SettingsNavigation()
        self.navigation.setObjectName("settingsNavigation")
        self.navigation.setMinimumWidth(170)
        self.navigation.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        for key, label in SETTINGS_SECTIONS:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            self.navigation.addItem(item)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setObjectName("settingsDetailScroll")
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.detail_contents = QWidget()
        self.detail_contents.setObjectName("settingsDetailContents")
        self.detail_layout = QVBoxLayout(self.detail_contents)
        self.detail_layout.setContentsMargins(0, self.SECTION_TOP_INSET, 6, 4)
        self.detail_layout.setSpacing(14)

        general, general_layout = self._create_section(
            "일반", "작업 실행과 편집 동작을 설정합니다."
        )
        self.minimize_checkbox = QCheckBox()
        self.minimize_checkbox.setObjectName("settingsCheckbox")
        self._add_setting_row(
            general_layout,
            "작업 실행 시 최소화",
            "작업을 시작할 때 대상 프로그램 창을 최소화합니다.",
            self.minimize_checkbox,
            control_right_margin=12,
        )
        self.task_minimize_app_checkbox = QCheckBox()
        self.task_minimize_app_checkbox.setObjectName("settingsCheckbox")
        self.task_minimize_app_checkbox.setAccessibleName("작업 실행 시 앱 최소화")
        self.task_minimize_app_row = self._add_setting_row(
            general_layout,
            "작업 실행 시 앱 최소화",
            "작업을 시작할 때 이 앱의 창을 한 번 최소화합니다.",
            self.task_minimize_app_checkbox,
            control_right_margin=12,
        )
        self.program_launch_checkbox = QCheckBox()
        self.program_launch_checkbox.setObjectName("settingsCheckbox")
        self._add_setting_row(
            general_layout,
            "자동 실행 작업",
            "작업 시작 전 대상 프로그램을 자동 실행하는 작업을 목록 최상단에 표시합니다.",
            self.program_launch_checkbox,
            control_right_margin=12,
        )
        self.runtime_edit_checkbox = QCheckBox()
        self.runtime_edit_checkbox.setObjectName("settingsCheckbox")
        self._add_setting_row(
            general_layout,
            "작업 중 옵션 편집",
            (
                "실행 중에도 작업 활성화, 순서, 세부 옵션 등 모든 실행 옵션을 "
                "편집합니다. 변경 사항은 다음 실행부터 적용됩니다."
            ),
            self.runtime_edit_checkbox,
            control_right_margin=12,
        )
        self.clear_log_checkbox = QCheckBox()
        self.clear_log_checkbox.setObjectName("settingsCheckbox")
        self._add_setting_row(
            general_layout,
            "작업 시작 시 로그 초기화",
            "새로운 작업을 시작할 때 이전 실행의 로그를 비웁니다.",
            self.clear_log_checkbox,
            control_right_margin=12,
        )

        program, program_layout = self._create_section(
            "시작 설정",
            "",
        )
        self.startup_section = program
        self.startup_checkboxes = {}
        self.startup_rows = {}
        for key, title, description in (
            ("launch_on_login", "", "현재 Windows 사용자 로그인 시 이 앱을 실행합니다."),
            ("minimize_app", "", "앱을 실행한 뒤 이 앱의 창을 최소화합니다."),
            ("auto_start_tasks", "", "앱을 실행된 후 현재 작업을 자동으로 실행합니다."),
            ("launch_program", "", "아래 경로로 프로그램을 실행합니다. ADB에서는 앱플레이어를 실행하며 게임 앱은 파이프라인에서 실행합니다."),
        ):
            checkbox = QCheckBox()
            checkbox.setObjectName("settingsCheckbox")
            checkbox.setAccessibleName(title)
            self.startup_checkboxes[key] = checkbox
            control = checkbox
            if key == "auto_start_tasks":
                self.auto_start_wait_spin = ClampedSecondsSpinBox()
                self.auto_start_wait_spin.setSuffix(" 초")
                self.auto_start_wait_spin.setAccessibleName("작업 자동 시작 대기 시간")
                control = self._startup_time_controls(self.auto_start_wait_spin, checkbox)
            self.startup_rows[key] = self._add_setting_row(
                program_layout, title, description, control, control_right_margin=12)
            if key == "launch_on_login":
                self.startup_registration_status = QLabel()
                self.startup_registration_status.setObjectName("settingsRowDescription")
                self.startup_registration_status.setTextFormat(Qt.TextFormat.PlainText)
                self.startup_registration_status.setWordWrap(True)
                self.startup_registration_status.hide()
                program_layout.addWidget(self.startup_registration_status)
            if key == "auto_start_tasks":
                self.daily_restart_time = NoWheelTimeEdit()
                self.daily_restart_time.setDisplayFormat("HH:mm")
                self.daily_restart_time.setAccessibleName("일일 초기화 시간")
                self.daily_restart_time.setToolTip("시스템 시간 기준입니다.")
                daily_checkbox = QCheckBox()
                daily_checkbox.setObjectName("settingsCheckbox")
                daily_checkbox.setAccessibleName("일일 초기화 재시작")
                self.startup_checkboxes["daily_restart"] = daily_checkbox
                daily_controls = self._startup_time_controls(self.daily_restart_time, daily_checkbox)
                self.startup_rows["daily_restart"] = self._add_setting_row(
                    program_layout, "일일 초기화 재시작",
                    "설정한 일일 초기화 시간에 맞춰 대상 프로그램 재실행 및 작업을 다시 수행합니다.",
                    daily_controls, control_right_margin=12)
                row = self.startup_rows["daily_restart"]
                row.title_label.setBuddy(daily_checkbox)
        self.program_path_divider = self._add_section_divider(program_layout)
        self.program_path_title = QLabel("프로그램 경로 설정")
        self.program_path_title.setObjectName("settingsRowTitle")
        program_layout.addWidget(self.program_path_title)
        self.program_path_description = QLabel("일부 기능을 사용하기 위해서는 대상 프로그램의 경로를 지정해야 합니다.")
        self.program_path_description.setObjectName("settingsRowDescription")
        self.program_path_description.setWordWrap(True)
        program_layout.addWidget(self.program_path_description)
        program_layout.addSpacing(8)
        self.program_auto_status = QLabel()
        self.program_auto_status.setObjectName("programPathStatus")
        self.program_auto_status.setWordWrap(True)
        program_layout.addWidget(self.program_auto_status)

        self.program_path_input = QLineEdit()
        self.program_path_input.setObjectName("programPathInput")
        self.program_path_input.setPlaceholderText(
            "실행 파일 또는 바로가기"
        )
        self.program_path_input.setMinimumWidth(180)
        self.program_browse_button = QPushButton("찾아보기")
        self.program_browse_button.setObjectName("programBrowseButton")
        self.program_apply_button = QPushButton("확인")
        self.program_apply_button.setObjectName("programApplyButton")
        self.program_test_button = QPushButton("테스트")
        self.program_test_button.setObjectName("programTestButton")
        self.program_test_button.setToolTip("현재 자동 경로 또는 확인된 수동 경로로 프로그램 실행을 요청합니다. 작업은 시작하지 않습니다.")
        self.program_test_button.setAccessibleName("프로그램 경로 실행 테스트")

        program_path_controls = QWidget()
        program_path_controls.setObjectName("programPathControls")
        program_path_layout = QHBoxLayout(program_path_controls)
        program_path_layout.setContentsMargins(0, 0, 0, 0)
        program_path_layout.setSpacing(6)
        program_path_layout.addWidget(self.program_path_input, 1)
        program_path_layout.addWidget(self.program_browse_button)
        program_path_layout.addWidget(self.program_apply_button)
        program_path_layout.addWidget(self.program_test_button)
        self.program_path_row = self._add_setting_row(
            program_layout,
            "수동 경로",
            WIN32_MANUAL_PATH_DESCRIPTION,
            program_path_controls,
        )
        self.program_path_row.setProperty("joinedProgramRow", True)
        self.program_path_row.layout().setContentsMargins(0, 8, 0, 8)
        program_path_controls.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.program_path_row.layout().setColumnStretch(0, 0)
        self.program_path_row.layout().setColumnStretch(1, 1)
        self.program_path_row.description_label.installEventFilter(self)
        self.program_path_row.description_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.program_active_status = QLabel()
        self.program_active_status.setObjectName("programPathStatus")
        self.program_active_status.setWordWrap(True)
        self.program_active_status_container = QWidget()
        self.program_active_status_container.setObjectName(
            "programActiveStatusContainer"
        )
        active_status_layout = QVBoxLayout(self.program_active_status_container)
        active_status_layout.setContentsMargins(0, 0, 0, 8)
        active_status_layout.setSpacing(0)
        active_status_layout.addWidget(self.program_active_status)
        self.program_active_status_effect = QGraphicsOpacityEffect(
            self.program_active_status_container
        )
        self.program_active_status_effect.setOpacity(1.0)
        self.program_active_status_container.setGraphicsEffect(
            self.program_active_status_effect
        )
        program_layout.addWidget(self.program_active_status_container)
        self._program_status_animation = None
        self._manual_path_error_active = False
        self.program_extra_input = QLineEdit()
        self.program_extra_input.setObjectName("programPathInput")
        self.program_extra_input.setPlaceholderText('예: --profile "사용자 1"')
        self.program_extra_input.setMinimumWidth(240)
        self.program_extra_row = self._add_setting_row(program_layout, "추가 인수", "수동 경로에 포함한 인수 뒤에 추가합니다. 바로가기 자체의 인수는 유지합니다.", self.program_extra_input)
        self.program_extra_row.setProperty("joinedProgramRow", True)
        self.program_extra_row.layout().setContentsMargins(0, 8, 0, 8)
        self.startup_wait_spin = NoWheelSpinBox()
        self.startup_wait_spin.setObjectName("settingsSpinBox")
        self.startup_wait_spin.setRange(0, 3600)
        self.startup_wait_spin.setSuffix(" 초")
        self.startup_wait_row = self._add_setting_row(program_layout, "앱플레이어 실행 대기", "ADB 앱플레이어 실행 요청 후 로딩을 기다릴 시간입니다.", self.startup_wait_spin)
        self.restart_connection_checkbox = QCheckBox()
        self.restart_connection_checkbox.setObjectName("settingsCheckbox")
        self.restart_row = self._add_setting_row(program_layout, "연결 실패 시 앱플레이어 재시작", "이 세션에서 직접 실행한 프로세스에 정상 종료를 요청하고 1회 재연결합니다. 런처가 다른 프로세스로 넘기거나 종료를 거부하면 생략합니다.", self.restart_connection_checkbox, control_right_margin=12)
        self.restart_connection_checkbox.setAccessibleName("연결 실패 시 앱플레이어 재시작")
        self.startup_warning_divider = self._add_section_divider(program_layout)
        self.startup_warning = QLabel("시작 동작·최소화·재시작은 일부 프로그램이나 바로가기에서 적용되지 않을 수 있습니다.")
        self.startup_warning.setObjectName("settingsRowDescription")
        self.startup_warning.setWordWrap(True)
        program_layout.addWidget(self.startup_warning)

        controller, controller_layout = self._create_section(
            "컨트롤러", "실행에 사용할 컨트롤러를 선택합니다."
        )
        self.controller_combo = PopupOnlyWheelComboBox()
        self.controller_combo.setObjectName("settingsComboBox")
        self.controller_combo.setMinimumWidth(240)
        if self.controllers:
            for controller_config in self.controllers:
                label = controller_config.get("label") or controller_config["name"]
                self.controller_combo.addItem(label, controller_config)
        else:
            self.controller_combo.addItem("사용 가능한 컨트롤러 없음", None)
            self.controller_combo.setEnabled(False)
        self._add_setting_row(
            controller_layout,
            "사용할 컨트롤러",
            "선택한 캡처 및 입력 방식은 다음 실행부터 적용됩니다.",
            self.controller_combo,
        )
        self.controller_details = QLabel()
        self.controller_details.setObjectName("controllerDetails")
        self.controller_details.setWordWrap(True)
        controller_layout.addWidget(self.controller_details)
        self._connection_layout = controller_layout

        appearance, appearance_layout = self._create_section(
            "외관", "애플리케이션의 표시 방식을 설정합니다."
        )
        self.theme_combo = PopupOnlyWheelComboBox()
        self.theme_combo.setObjectName("settingsComboBox")
        self.theme_combo.setMinimumWidth(180)
        for label, value in (
            ("시스템 설정", "system"),
            ("라이트", "light"),
            ("다크", "dark"),
        ):
            self.theme_combo.addItem(label, value)
        self._add_setting_row(
            appearance_layout,
            "색상 모드",
            "애플리케이션 전체와 Windows 제목 표시줄의 색상을 변경합니다.",
            self.theme_combo,
        )

        _management, management_layout = self._create_section("관리", "사용자 설정을 초기화하고 누적된 로그와 캐시를 정리합니다.")
        self.management = ManagementPanel(self)
        management_layout.addWidget(self.management)

        # The card owns its bottom inset; avoid doubling it on a final row.
        for section in self._sections:
            section_layout = section.layout()
            last_widget = section_layout.itemAt(section_layout.count() - 1).widget()
            if last_widget is not None and last_widget.objectName() == "settingsRow":
                margins = last_widget.layout().contentsMargins()
                last_widget.layout().setContentsMargins(
                    margins.left(), margins.top(), margins.right(), 0
                )

        self._section_widgets = {key: section for (key, _label), section in zip(SETTINGS_SECTIONS, self._sections)}
        for key, section in self._section_widgets.items():
            section.setProperty("settingsSectionKey", key)
        items = {item.data(Qt.ItemDataRole.UserRole): item
                 for item in [self.navigation.takeItem(0) for _ in range(self.navigation.count())]}
        for key in normalize_section_order(self.config.get("section_order")):
            self.navigation.addItem(items[key])
        self._reorder_section_widgets()
        self.detail_layout.addStretch(1)
        self.detail_scroll.setWidget(self.detail_contents)

        root_layout.addWidget(self.navigation, 2)
        root_layout.addWidget(self.detail_scroll, 8)

    def set_app_title(self, title):
        self.app_title = display_name(title)
        self.startup_section.description_label.setTextFormat(Qt.TextFormat.PlainText)
        self.startup_section.description_label.setText(
            f"{self.app_title} 시작 동작과 자동 실행할 대상 프로그램을 설정합니다.\n"
            f"변경한 시작 동작은 다음 {self.app_title} 실행부터 적용됩니다.")
        labels = {
            "launch_on_login": f"시스템 시작 시 {self.app_title} 실행",
            "minimize_app": f"{self.app_title} 실행 후 최소화",
            "auto_start_tasks": f"{self.app_title} 실행 후 작업 자동 시작",
            "launch_program": f"{self.app_title} 실행 후 대상 프로그램 실행",
        }
        for key, text in labels.items():
            label = self.startup_rows[key].title_label
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setText(text)
            self.startup_checkboxes[key].setAccessibleName(text)

    def _create_section(self, title, description):
        section = QFrame()
        section.setObjectName("settingsSection")
        section.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(section)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(0)

        title_label = QLabel(title)
        title_label.setObjectName("settingsSectionTitle")
        layout.addWidget(title_label)
        layout.addSpacing(3)

        description_label = QLabel(description)
        description_label.setObjectName("settingsSectionDescription")
        description_label.setWordWrap(True)
        layout.addWidget(description_label)
        section.description_label = description_label
        layout.addSpacing(6)

        self.detail_layout.addWidget(section)
        self._sections.append(section)
        return section, layout

    @staticmethod
    def _startup_time_controls(editor, checkbox):
        editor.setObjectName("settingsSpinBox")
        editor.setFixedWidth(STARTUP_TIME_INPUT_WIDTH)
        controls = QWidget()
        layout = QHBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(editor)
        layout.addWidget(checkbox)
        return controls

    @staticmethod
    def _add_section_divider(layout):
        divider = QFrame()
        divider.setObjectName("settingsDivider")
        divider.setFixedHeight(1)
        layout.addWidget(divider)
        layout.addSpacing(12)
        return divider

    @staticmethod
    def _add_setting_row(
        parent_layout,
        title,
        description,
        control,
        control_right_margin=0,
    ):
        row = QFrame()
        row.setObjectName("settingsRow")
        row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        row_layout = QGridLayout(row)
        row_layout.setContentsMargins(0, 12, control_right_margin, 12)
        row_layout.setHorizontalSpacing(16)
        row_layout.setVerticalSpacing(2)
        row_layout.setColumnStretch(0, 1)

        toggle = control if isinstance(control, QCheckBox) else control.findChild(QCheckBox)
        label_type = AssociatedControlLabel if toggle is not None else QLabel
        title_label = label_type(title)
        title_label.setObjectName("settingsRowTitle")
        title_label.setTextFormat(Qt.TextFormat.PlainText)
        description_label = label_type(description)
        description_label.setObjectName("settingsRowDescription")
        description_label.setWordWrap(True)

        if toggle is not None:
            title_label.activated.connect(toggle.click)
            description_label.activated.connect(toggle.click)

        control.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row_layout.addWidget(title_label, 0, 0)
        row_layout.addWidget(description_label, 1, 0)
        row_layout.addWidget(
            control,
            0,
            1,
            2,
            1,
            Qt.AlignmentFlag.AlignVCenter,
        )
        parent_layout.addWidget(row)
        row.title_label = title_label
        row.description_label = description_label
        return row

    def _apply_config(self):
        general = self.config["general"]
        self.minimize_checkbox.setChecked(general["minimize_enabled"])
        self.task_minimize_app_checkbox.setChecked(general["minimize_app_on_task_start"])
        self.program_launch_checkbox.setChecked(
            general["program_launch_task_enabled"]
        )
        self.runtime_edit_checkbox.setChecked(
            general["allow_option_edits_while_running"]
        )
        self.clear_log_checkbox.setChecked(general["clear_log_on_start"])

        controller_index = self._find_controller_index(self.config["controller"])
        if controller_index >= 0:
            self.controller_combo.setCurrentIndex(controller_index)
        self._confirmed_manual_path = self.config["program"]["manual_path"]
        self.program_path_input.setText(self._confirmed_manual_path)
        self.program_extra_input.setText(self.config["program"]["extra_arguments"])
        startup = normalize_startup_config(self.config.get("startup"))
        for key, checkbox in self.startup_checkboxes.items():
            checkbox.setChecked(startup[key])
        self.auto_start_wait_spin.setValue(startup["task_wait_seconds"])
        self.daily_restart_time.setTime(QTime.fromString(startup["daily_restart_time"], "HH:mm"))
        self.startup_wait_spin.setValue(startup["emulator_wait_seconds"])
        self.restart_connection_checkbox.setChecked(startup["restart_on_connection_failure"])
        try:
            registered = self.startup_registration.is_enabled()
            self.startup_checkboxes["launch_on_login"].setChecked(registered)
            self.config["startup"]["launch_on_login"] = registered
        except (OSError, ValueError, ImportError) as error:
            self.startup_registration_status.setText(f"시스템 시작 등록 상태를 확인하지 못했습니다: {error}")
            self.startup_registration_status.show()
        self._refresh_program_status()

        theme = self.config["appearance"]["theme"]
        theme_index = self.theme_combo.findData(theme)
        self.theme_combo.setCurrentIndex(theme_index if theme_index >= 0 else 1)

        self._update_controller_details()

    def _find_controller_index(self, saved_controller):
        if not isinstance(saved_controller, dict):
            return 0 if self.controllers else -1

        saved_name = saved_controller.get("name")
        if isinstance(saved_name, str):
            for index, controller in enumerate(self.controllers):
                if controller.get("name") == saved_name:
                    return index

        saved_win32 = saved_controller.get("win32")
        if not isinstance(saved_win32, dict):
            return 0 if self.controllers else -1
        requested = {
            method: saved_win32[method]
            for method in WIN32_METHOD_PRIORITY
            if isinstance(saved_win32.get(method), str) and saved_win32[method]
        }
        for index, controller in enumerate(self.controllers):
            if controller.get("type") != "Win32":
                continue
            win32_config = controller["win32"]
            if requested and all(
                win32_config.get(method, WIN32_METHOD_DEFAULTS[method]) == value
                for method, value in requested.items()
            ):
                return index
        return 0 if self.controllers else -1

    def _connect_controls(self):
        self.navigation.model().rowsAboutToBeMoved.connect(self._begin_section_move)
        self.navigation.model().rowsMoved.connect(self._finish_section_move)
        self.navigation.currentRowChanged.connect(self._scroll_to_section)
        self.detail_scroll.verticalScrollBar().valueChanged.connect(
            self._sync_navigation_to_scroll
        )
        self.minimize_checkbox.toggled.connect(self._on_minimize_changed)
        self.task_minimize_app_checkbox.toggled.connect(self._save)
        self.program_launch_checkbox.toggled.connect(
            self._on_program_launch_enabled_changed
        )
        self.runtime_edit_checkbox.toggled.connect(self._on_runtime_edit_changed)
        self.clear_log_checkbox.toggled.connect(self._on_clear_log_changed)
        self.program_browse_button.clicked.connect(self._browse_program_path)
        self.program_apply_button.clicked.connect(self._apply_program_path)
        self.program_test_button.clicked.connect(self._test_program_path)
        self.controller_combo.currentIndexChanged.connect(
            self._on_controller_changed
        )
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.navigation.setCurrentRow(0)
        for key, checkbox in self.startup_checkboxes.items():
            checkbox.toggled.connect(lambda checked, setting=key: self._on_startup_changed(setting, checked))
        self.restart_connection_checkbox.toggled.connect(self._save)
        self.startup_wait_spin.valueChanged.connect(self._save)
        self.auto_start_wait_spin.valueChanged.connect(self._save)
        self.daily_restart_time.timeChanged.connect(self._save)
        self.program_extra_input.textChanged.connect(self._on_extra_arguments_changed)
        for section in self._sections:
            self._watch_section_interactions(section)

    def _watch_section_interactions(self, widget):
        # Watch the controls, their labels, and viewports without filtering the
        # whole application. Late-added connection controls use the same hook.
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def _highlight_setting_section(self, widget):
        if self._syncing_navigation:
            return
        while isinstance(widget, QWidget) and widget is not self:
            key = widget.property("settingsSectionKey")
            if key in self._section_widgets and self._section_widgets[key] is widget:
                row = self.section_order().index(key)
                self._syncing_navigation = True
                try:
                    # Changing the active editor must not scroll the card to
                    # the top as selecting a navigation entry normally does.
                    self.navigation.setCurrentRow(row)
                finally:
                    self._syncing_navigation = False
                return
            widget = widget.parentWidget()

    def startup_settings(self):
        return {**{key: checkbox.isChecked() for key, checkbox in self.startup_checkboxes.items()},
                "restart_on_connection_failure": self.restart_connection_checkbox.isChecked(),
                "emulator_wait_seconds": self.startup_wait_spin.value(),
                "task_wait_seconds": self.auto_start_wait_spin.value(),
                "daily_restart_time": self.daily_restart_time.time().toString("HH:mm")}

    def _on_startup_changed(self, key, checked):
        if key == "launch_on_login":
            try:
                self.startup_registration.set_enabled(checked)
            except (OSError, ValueError) as error:
                checkbox = self.startup_checkboxes[key]
                checkbox.blockSignals(True)
                checkbox.setChecked(not checked)
                checkbox.blockSignals(False)
                self.startup_registration_status.setText(f"시스템 시작 등록 변경 실패: {error}")
                self.startup_registration_status.show()
                return
            self.startup_registration_status.clear()
            self.startup_registration_status.hide()
        self._save()

    def _on_extra_arguments_changed(self, _text):
        self._save()
        settings = self.program_settings()
        if settings.get("arguments_error"):
            self._show_program_test_status(settings["arguments_error"], False)
        self.program_changed.emit(settings)

    def section_order(self):
        return [self.navigation.item(row).data(Qt.ItemDataRole.UserRole) for row in range(self.navigation.count())]

    def _reorder_section_widgets(self):
        self._sections = [self._section_widgets[key] for key in self.section_order()]
        for section in self._sections:
            self.detail_layout.removeWidget(section)
        for row, section in enumerate(self._sections):
            self.detail_layout.insertWidget(row, section)
        self.detail_layout.activate()

    def _begin_section_move(self, *_args):
        self._syncing_navigation = True

    def _finish_section_move(self, *_args):
        self._reorder_section_widgets()
        self._syncing_navigation = False
        self._scroll_to_section(self.navigation.currentRow())
        self._save()

    def _scroll_to_section(self, row):
        if self._syncing_navigation or not 0 <= row < len(self._sections):
            return
        section = self._sections[row]
        # Keep an explicit selection even when scrolling to it is clamped.
        self._syncing_navigation = True
        try:
            self.detail_scroll.verticalScrollBar().setValue(
                max(0, section.y() - self.SECTION_TOP_INSET)
            )
        finally:
            self._syncing_navigation = False

    def _sync_navigation_to_scroll(self, value):
        if self._syncing_navigation or not self._sections:
            return
        # A partially visible card is still the topmost visible section.
        active_row = len(self._sections) - 1
        for index, section in enumerate(self._sections):
            if section.y() + section.height() > value:
                active_row = index
                break

        if self.navigation.currentRow() == active_row:
            return
        self._syncing_navigation = True
        self.navigation.setCurrentRow(active_row)
        self._syncing_navigation = False

    def _collect_config(self):
        return {
            "section_order": self.section_order(),
            "startup": self.startup_settings(),
            "general": {
                "minimize_enabled": self.minimize_checkbox.isChecked(),
                "minimize_app_on_task_start": self.task_minimize_app_checkbox.isChecked(),
                "program_launch_task_enabled": (
                    self.program_launch_checkbox.isChecked()
                ),
                "allow_option_edits_while_running": (
                    self.runtime_edit_checkbox.isChecked()
                ),
                "clear_log_on_start": self.clear_log_checkbox.isChecked(),
            },
            "controller": SettingsStore.serialize_controller(
                self.controller_combo.currentData()
            ),
            "connection": normalize_connection_preferences(self.config.get("connection")),
            "monitor": normalize_screen_preferences(self.config.get("monitor")),
            "program": {
                **normalize_program_config(self.config.get("program")),
                "manual_path": self._confirmed_manual_path,
                "extra_arguments": self.program_extra_input.text().strip(),
            },
            "appearance": {
                "theme": self.theme_combo.currentData() or "light",
            },
        }

    def add_connection_panel(self, panel):
        group = QFrame()
        group.setObjectName("settingsConnectionGroup")
        group.setProperty("connectionGroup", True)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)
        label = QLabel("연결 설정 · 사전 확인")
        label.setObjectName("settingsRowTitle")
        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        label.setMinimumHeight(24)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        panel.layout().setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label)
        layout.addWidget(panel)
        group.title_label = label
        self._connection_group = group
        self._connection_layout.addWidget(group)
        self._watch_section_interactions(group)

    def save_monitor_preferences(self, connection=None, screen=None):
        if connection is not None:
            self.config["connection"] = normalize_connection_preferences(connection)
        if screen is not None:
            self.config["monitor"] = normalize_screen_preferences(screen)
        self._save()

    def _save(self):
        self._sync_launch_requirements()
        self.config = self._collect_config()
        self.store.save(self.config)
        self.startup_changed.emit()

    def _sync_launch_requirements(self):
        available = bool(self.program_settings().get("resolved_path"))
        changed = False
        for key in ("auto_start_tasks", "launch_program", "daily_restart"):
            checkbox = self.startup_checkboxes.get(key)
            if checkbox is None:
                continue
            if not available and checkbox.isChecked():
                checkbox.blockSignals(True)
                checkbox.setChecked(False)
                checkbox.blockSignals(False)
                changed = True
            self.startup_rows[key].setEnabled(available)
            checkbox.setToolTip("" if available else "프로그램 경로 설정에서 자동 또는 수동 실행 경로를 먼저 확인하세요.")
        return changed

    def _on_minimize_changed(self, enabled):
        self._save()
        self.minimize_changed.emit(enabled)

    def _on_program_launch_enabled_changed(self, enabled):
        self._save()
        self.program_launch_task_enabled_changed.emit(enabled)

    def _on_runtime_edit_changed(self, enabled):
        self._save()
        self.runtime_option_editing_changed.emit(enabled)

    def _on_clear_log_changed(self, _enabled):
        self._save()

    def _browse_program_path(self):
        adb = self.controller_settings().get("type") == "Adb"
        selected, _ = QFileDialog.getOpenFileName(
            self, "앱플레이어 실행 파일 선택" if adb else "프로그램 실행 파일 선택", "",
            "실행 파일 및 바로가기 (*.exe *.lnk *.url *.com *.cmd *.bat);;모든 파일 (*)",
            options=QFileDialog.Option.DontResolveSymlinks,
        )
        if selected:
            self.program_path_input.setText(f'"{selected}"')

    def _apply_program_path(self):
        if self._confirmed_manual_path or self._manual_path_error_active:
            self._confirmed_manual_path = ""
            self._manual_path_error_active = False
            self.program_path_input.clear()
            self._save()
            self._refresh_program_status(animate=True)
            self.program_changed.emit(self.program_settings())
            return

        manual_path = self.program_path_input.text().strip()
        error = None
        if self.controller_settings().get("type") == "Adb":
            try:
                parse_manual_program_command(manual_path)
            except (OSError, ValueError) as exception:
                error = str(exception)
        elif manual_path and resolve_manual_program_path(manual_path) is None:
            error = "실행 파일을 확인할 수 없습니다. 파일 경로와 인수를 확인하세요."
        if error:
            self._manual_path_error_active = True
            self.program_apply_button.setText("초기화")
            self._set_status_label(
                self.program_active_status,
                error,
                False,
            )
            self._set_program_active_status_visible(True, animate=True)
            self.program_changed.emit(self.program_settings())
            return

        self._confirmed_manual_path = manual_path
        self._manual_path_error_active = False
        self._save()
        self._refresh_program_status(animate=True)
        self.program_changed.emit(self.program_settings())

    @property
    def program_test_busy(self):
        return self._program_test_worker is not None

    def set_program_test_allowed(self, allowed):
        self._program_test_allowed = bool(allowed)
        busy = self.program_test_busy or self._program_test_closing
        self.program_test_button.setEnabled(not busy and self._program_test_allowed)
        for control in (self.program_path_input, self.program_browse_button, self.program_apply_button):
            control.setEnabled(not busy)
        self.program_extra_input.setEnabled(not busy)
        self.controller_combo.setEnabled(not busy and bool(self.controllers))

    def _program_test_context(self):
        return (self.program_path_input.text().strip(), self._confirmed_manual_path,
                self.controller_settings().get("name"), self.program_extra_input.text())

    def _show_program_test_status(self, message, succeeded=None):
        self._set_status_label(self.program_active_status, message, succeeded)
        self._set_program_active_status_visible(True, animate=True)

    def _test_program_path(self):
        if self.program_test_busy or self._program_test_closing:
            return
        if not self._program_test_allowed or not self.program_test_guard():
            self._show_program_test_status("작업과 대상 탐색이 끝난 뒤 실행 테스트를 사용하세요.", False)
            return
        if self.program_path_input.text().strip() != self._confirmed_manual_path:
            self._show_program_test_status("수동 경로를 확인한 뒤 테스트하세요.", False)
            return
        settings = self.program_settings()
        if not settings.get("resolved_path"):
            self._show_program_test_status("실행할 경로가 없습니다. 자동 검색 결과 또는 수동 경로를 확인하세요.", False)
            return
        self._program_test_snapshot = self._program_test_context()
        self._program_test_worker = worker = ProgramTestWorker(settings, self)
        worker.finished.connect(self._on_program_test_finished)
        self.set_program_test_allowed(self._program_test_allowed)
        self._show_program_test_status("프로그램 실행을 요청하고 있습니다…")
        self.program_test_busy_changed.emit(True)
        try:
            worker.start()
        except RuntimeError as error:
            self._program_test_worker = None
            worker.deleteLater()
            self.set_program_test_allowed(self._program_test_allowed)
            self._show_program_test_status(f"실행 테스트 실패: {error}", False)
            self.program_test_busy_changed.emit(False)

    @Slot()
    def _on_program_test_finished(self):
        worker = self._program_test_worker
        if worker is None:
            return
        self._program_test_worker = None
        succeeded, message = worker.result
        worker.deleteLater()
        self.set_program_test_allowed(self._program_test_allowed)
        if not self._program_test_closing and self._program_test_snapshot == self._program_test_context():
            self._show_program_test_status(message, succeeded)
        self.program_test_busy_changed.emit(False)
        if self._program_test_closing:
            self.program_test_shutdown_ready.emit()

    def shutdown_program_test(self):
        self._program_test_closing = True
        self.set_program_test_allowed(False)
        if self._program_test_worker is not None:
            self._program_test_worker.requestInterruption()
        return not self.program_test_busy

    @staticmethod
    def _refresh_status_style(label):
        label.style().unpolish(label)
        label.style().polish(label)
        label.update()

    def _set_status_label(self, label, text, is_valid):
        if label.text() == text and label.property("pathValid") == is_valid:
            return
        label.setText(text)
        label.setProperty("pathValid", is_valid)
        self._refresh_status_style(label)

    def _set_program_active_status_visible(self, visible, animate=False):
        container = self.program_active_status_container
        opacity_effect = self.program_active_status_effect
        if self._program_status_animation is not None:
            self._program_status_animation.stop()
            self._program_status_animation.deleteLater()
            self._program_status_animation = None

        if not animate or not self.isVisible():
            container.setVisible(visible)
            opacity_effect.setOpacity(1.0)
            container.updateGeometry()
            return

        if visible:
            if not container.isVisible():
                opacity_effect.setOpacity(0.0)
                container.show()
            start_opacity = opacity_effect.opacity()
            target_opacity = 1.0
        else:
            if not container.isVisible():
                opacity_effect.setOpacity(1.0)
                return
            start_opacity = opacity_effect.opacity()
            target_opacity = 0.0

        # 레이아웃 높이는 매 프레임 바꾸지 않고 새 영역만 페이드하여
        # 기존 설정 카드들이 반복해서 다시 그려지는 현상을 방지한다.
        animation = QPropertyAnimation(opacity_effect, b"opacity", self)
        animation.setDuration(140)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        animation.setStartValue(start_opacity)
        animation.setEndValue(target_opacity)

        def finish_transition():
            if not visible:
                container.hide()
            opacity_effect.setOpacity(1.0)
            container.updateGeometry()
            if self._program_status_animation is animation:
                self._program_status_animation = None
            animation.deleteLater()

        animation.finished.connect(finish_transition)
        self._program_status_animation = animation
        animation.start()

    def _refresh_program_status(self, animate=False):
        config = {
            **normalize_program_config(self.config.get("program")),
            "manual_path": self._confirmed_manual_path,
            "controller": self.controller_settings(),
        }
        has_manual_path = bool(self._confirmed_manual_path)
        show_active_status = has_manual_path or self._manual_path_error_active
        self.program_apply_button.setText(
            "초기화" if show_active_status else "확인"
        )
        adb = self.controller_settings().get("type") == "Adb"
        self.program_path_row.description_label.setText(ADB_MANUAL_PATH_DESCRIPTION if adb else WIN32_MANUAL_PATH_DESCRIPTION)
        self._update_program_path_layout()
        self.program_path_input.setPlaceholderText(
            '"파일 경로" 실행 인수'
            if adb else "실행 파일 또는 바로가기"
        )
        self.program_auto_status.setEnabled(not adb)
        auto_path = None if adb else find_auto_program_executable(config)
        self._auto_program_path = auto_path
        if adb:
            self._set_status_label(self.program_auto_status, "자동 경로를 사용할 수 없습니다.", None)
        elif auto_path is None:
            self._set_status_label(
                self.program_auto_status,
                "자동 검색: 대상을 확정하지 못했습니다. 수동 경로를 지정하세요.",
                False,
            )
        else:
            self._set_status_label(
                self.program_auto_status,
                f"자동 검색: {auto_path}" + (" (Steam 설치 폴더)" if auto_path.is_dir() else ""),
                True,
            )

        if self._sync_launch_requirements() and self._controls_connected:
            self._save()

        if self._manual_path_error_active:
            self._set_program_active_status_visible(True, animate)
            return
        if not has_manual_path and not adb:
            # 접히는 동안 마지막 수동 경로 상태를 유지해 색상과 문구가 튀지 않게 한다.
            self._set_program_active_status_visible(False, animate)
            return

        active_path = self.program_settings().get("resolved_path")
        if not active_path:
            self._set_status_label(
                self.program_active_status,
                "사용할 실행 파일이 없습니다. 작업 목록의 자동 실행 작업이 비활성화됩니다.",
                False,
            )
        else:
            self._set_status_label(
                self.program_active_status,
                f"사용 경로: {active_path}",
                True,
            )
        self._set_program_active_status_visible(True, animate)

    def _update_program_path_layout(self):
        label = self.program_path_row.description_label
        label.setMinimumHeight(0)
        # Reserve two readable description lines; the path field gets spare width.
        minimum_width = max(1, self.program_path_row.title_label.sizeHint().width())
        maximum_width = max(minimum_width, label.fontMetrics().horizontalAdvance(label.text()))
        two_line_height = 2 * label.fontMetrics().lineSpacing()
        while minimum_width < maximum_width:
            width = (minimum_width + maximum_width) // 2
            if label.heightForWidth(width) <= two_line_height:
                maximum_width = width
            else:
                minimum_width = width + 1
        self.program_path_row.layout().setColumnMinimumWidth(0, minimum_width + 4)
        label.setMinimumHeight(max(0, label.heightForWidth(max(1, label.width()))))

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn, QEvent.Type.KeyPress):
            self._highlight_setting_section(watched)
        if (hasattr(self, "program_path_row") and watched is self.program_path_row.description_label
                and event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange)):
            self._update_program_path_layout()
        return super().eventFilter(watched, event)

    def _on_controller_changed(self, _index):
        self._update_controller_details()
        self._manual_path_error_active = False
        self._refresh_program_status()
        self._save()
        self.controller_changed.emit(self.controller_settings())
        self.program_changed.emit(self.program_settings())

    def _on_theme_changed(self, _index):
        self._save()
        self.theme_changed.emit(self.theme())

    def _update_controller_details(self):
        controller = self.controller_combo.currentData()
        adb = isinstance(controller, dict) and controller.get("type") == "Adb"
        self.startup_wait_row.setVisible(adb)
        self.restart_row.setVisible(adb)
        if not isinstance(controller, dict):
            self.controller_details.setText("선택할 수 있는 컨트롤러가 없습니다.")
            return
        if controller.get("type") == "Adb":
            self.controller_details.setText(f"ID: {controller['name']}  ·  ADB 기기 화면 캡처 및 입력")
            return
        win32_config = controller.get("win32", {})
        values = {
            method: win32_config.get(method, WIN32_METHOD_DEFAULTS[method])
            for method in WIN32_METHOD_PRIORITY
        }
        self.controller_details.setText(
            f"ID: {controller['name']}  ·  캡처: {values['screencap']}  ·  "
            f"마우스: {values['mouse']}  ·  키보드: {values['keyboard']}"
        )

    def minimize_enabled(self):
        return self.minimize_checkbox.isChecked()

    def minimize_app_on_task_start_enabled(self):
        return self.task_minimize_app_checkbox.isChecked()

    def set_minimize_enabled(self, enabled):
        self.minimize_checkbox.setChecked(bool(enabled))

    def program_launch_task_enabled(self):
        return self.program_launch_checkbox.isChecked()

    def runtime_option_editing_enabled(self):
        return self.runtime_edit_checkbox.isChecked()

    def clear_log_on_start_enabled(self):
        return self.clear_log_checkbox.isChecked()

    def theme(self):
        return self.theme_combo.currentData() or "light"

    def controller_settings(self):
        return SettingsStore.serialize_controller(
            self.controller_combo.currentData()
        )

    def program_settings(self):
        config = {
            **normalize_program_config(self.config.get("program")),
            "manual_path": self._confirmed_manual_path,
            "controller": self.controller_settings(),
        }
        adb = self.controller_settings().get("type") == "Adb"
        config["manual_only"] = adb
        config["connection"] = normalize_connection_preferences(self.config.get("connection"))
        if adb:
            try:
                resolved_path, arguments = parse_manual_program_command(self._confirmed_manual_path)
                config["launch_arguments"] = arguments
            except (OSError, ValueError):
                resolved_path = None
                config["launch_arguments"] = []
        else:
            if self._confirmed_manual_path:
                resolved_path = find_program_executable(config)
            else:
                # Discovery runs on status/controller refresh, not every snapshot/FPS/log event.
                resolved_path = getattr(self, "_auto_program_path", None)
                if resolved_path and not (resolved_path.is_file() or resolved_path.is_dir()):
                    resolved_path = None
            config["launch_arguments"] = []
            if self._confirmed_manual_path and resolved_path:
                try:
                    _, config["launch_arguments"] = parse_manual_program_command(self._confirmed_manual_path)
                except (OSError, ValueError):
                    pass  # Existing unquoted EXE paths and single-EXE folders have no arguments.
        if self._manual_path_error_active:
            resolved_path = None
            config["launch_arguments"] = []
        config["resolved_path"] = str(resolved_path) if resolved_path else ""
        config["adb_startup_wait_seconds"] = self.startup_wait_spin.value()
        config["restart_on_connection_failure"] = self.restart_connection_checkbox.isChecked() and adb
        try:
            config["launch_arguments"] += parse_extra_arguments(self.program_extra_input.text())
            if resolved_path and Path(resolved_path).suffix.casefold() == ".url" and config["launch_arguments"]:
                raise ValueError("인터넷 바로가기(.url)에는 추가 인수를 사용할 수 없습니다.")
        except (OSError, ValueError) as error:
            config["arguments_error"] = f"추가 인수 형식을 확인하세요: {error}"
            config["resolved_path"] = ""
        return config
