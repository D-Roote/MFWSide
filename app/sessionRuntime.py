"""Explicit-target session runtime; preserve the established Win32 lifecycle."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from maa.controller import AdbController

from app.monitoring import supported_presets, window_process_info
from app.runtime import AppRuntime, ControllerActivitySink, SW_SHOWMINNOACTIVE


def target_identity(connection):
    target = connection.get("target") if isinstance(connection, dict) else None
    if not isinstance(target, dict):
        return None
    if target.get("kind") == "Win32":
        hwnd, pid = target.get("hwnd"), target.get("pid")
        if type(hwnd) is int and hwnd > 0 and type(pid) is int and pid > 0:
            return f"Win32:{pid}:{hwnd}"
    elif target.get("kind") == "Adb" and isinstance(target.get("key"), str) and target["key"].strip():
        return "Adb:" + target["key"].strip()
    return None


def serialize_target(target):
    result = {key: getattr(target, key) for key in ("key", "label", "kind", "hwnd", "pid", "process_path")}
    if target.device is not None:
        device = target.device
        result["device"] = {
            "adb_path": str(device.adb_path), "address": device.address,
            "screencap_methods": int(device.screencap_methods),
            "input_methods": int(device.input_methods), "config": deepcopy(device.config),
        }
    return result


def resolve_preset(snapshot, connection):
    for preset in supported_presets(snapshot["interface"], snapshot["resource"]):
        if preset["name"] == connection.get("preset"):
            if not target_identity(connection) or preset["type"] != connection["target"]["kind"]:
                break
            return preset
    raise ValueError("현재 리소스가 지원하는 컨트롤러와 연결 대상을 선택하세요.")


def needs_desktop_lock(preset, minimize=False):
    if preset["type"] != "Win32":
        return False
    config = preset["win32"]
    methods = (config.get("mouse", "PostMessageWithWindowPos"), config.get("keyboard", "PostMessage"))
    return minimize or any("With" in method or method in ("Seize", "LegacyEvent", "Interception")
                           for method in methods)


class SessionRuntime(AppRuntime):
    def __init__(self, snapshot, connection, user_dir):
        self.snapshot = deepcopy(snapshot)
        self.connection = deepcopy(connection)
        self.session_preset = resolve_preset(snapshot, connection)
        super().__init__()
        self.interface_path = Path(snapshot["interface_path"])
        self.user_dir = Path(user_dir)

    def _load_interface(self):
        return deepcopy(self.snapshot["interface"])

    def _get_resource_config(self):
        return deepcopy(self.snapshot["resource"])

    def _select_controller_config(self, controller_settings=None):
        return deepcopy(self.session_preset), "exact"

    @property
    def is_adb(self):
        return self.session_preset["type"] == "Adb"

    def _find_target_window(self, *args, **kwargs):
        target = self.connection["target"]
        if self.is_adb:
            return None, "ADB 세션은 Win32 창을 탐색하지 않습니다."
        hwnd = target["hwnd"]
        pid, path = window_process_info(hwnd)
        if not pid or pid != target["pid"] or (target.get("process_path") and path.casefold() != target["process_path"].casefold()):
            return None, "대상 창이 종료되거나 변경되었습니다. 다시 연결하세요."
        return SimpleNamespace(hwnd=hwnd), "세션 대상 창을 확인했습니다."

    def _launch_program(self, program_settings):
        self._program_started_for_session = False
        if self.is_adb:
            self.log_sink._emit("ADB 세션은 EXE 자동 실행을 생략합니다. 앱 실행은 작업 파이프라인을 사용합니다.")
            return True, "ADB 연결 대상이 지정되어 있습니다."
        window, message = self._find_target_window()
        return window is not None, message

    def _create_controller(self, controller_settings=None, **kwargs):
        if not self.is_adb:
            window, message = self._find_target_window()
            if window is None:
                return False, message
            kwargs["window"] = window
            return super()._create_controller(controller_settings, **kwargs)
        target = self.connection["target"]
        device = target.get("device", {})
        preferences = self.connection.get("preferences", {})
        adb_path = preferences.get("adb_path") or device.get("adb_path", "")
        if not adb_path or not Path(adb_path).is_file():
            return False, "ADB 실행 파일 경로를 확인하세요."
        # Discovered method masks/config belong to the discovered ADB binary.
        if device.get("adb_path") and Path(adb_path) != Path(device["adb_path"]):
            device = {}
        params = {key: deepcopy(device[key]) for key in ("screencap_methods", "input_methods", "config") if key in device}
        self.controller = AdbController(adb_path=adb_path, address=target["key"], **params)
        self._controller_activity = ControllerActivitySink()
        self._controller_sink_id = self.controller.add_sink(self._controller_activity)
        if self._controller_sink_id is None:
            return False, "컨트롤러 완료 콜백 연결에 실패했습니다."
        return True, "ADB 컨트롤러를 생성했습니다."

    def _resize_window_for_task(self, *args, **kwargs):
        return True if self.is_adb else super()._resize_window_for_task(*args, **kwargs)

    def _minimize_window_for_task(self):
        if not self.is_adb:
            return super()._minimize_window_for_task()
        emulator = self.connection.get("emulator") or {}
        if not emulator:
            self.log_sink._emit("ADB는 창 최소화 없이 작업할 수 있습니다. 편의 최소화 창은 선택하지 않았습니다.")
            return True
        hwnd, expected_pid = emulator.get("hwnd"), emulator.get("pid")
        if type(hwnd) is not int or hwnd <= 0 or type(expected_pid) is not int or expected_pid <= 0:
            return False
        pid, path = window_process_info(hwnd)
        if not pid or pid != expected_pid or (emulator.get("path") and path.casefold() != emulator["path"].casefold()):
            return False
        self._user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
        return bool(self._user32.IsIconic(hwnd))

    def _get_controller_log_message(self):
        if self.is_adb:
            return f"ADB 컨트롤러 연결: {self.session_preset['name']} · {self.connection['target']['key']}"
        return super()._get_controller_log_message()
