"""ADB target preflight and cancelable Windows app-player startup, no game input."""
from copy import deepcopy
from pathlib import Path
import subprocess
import time

from maa.toolkit import Toolkit
from app.pg_init import parse_extra_arguments, parse_manual_program_command
from app.programLaunch import is_shortcut, launch_shortcut
from app.adbWindow import bind_runtime_window


ADB_POST_PROCESS_WAIT_SECONDS = 60.0


def prepare_adb_connection(preferences, known_target=None):
    from app.monitoring import normalize_connection_preferences
    preferences = normalize_connection_preferences(preferences)
    address = preferences["address"]
    if not address:
        raise ValueError("연결 설정에서 실행할 ADB 기기 주소를 먼저 선택하거나 입력하세요.")
    target = {"kind": "Adb", "key": address, "label": address}
    if (isinstance(known_target, dict) and known_target.get("kind") == "Adb"
            and known_target.get("key") == address):
        target = deepcopy(known_target)
    if not preferences["adb_path"] and not (target.get("device") or {}).get("adb_path"):
        device = next((device for device in Toolkit.find_adb_devices() if device.address == address), None)
        if device is None:
            raise ValueError("자동 실행 전에 연결 설정의 ADB 실행 파일 경로를 입력하세요.")
        target["device"] = {"adb_path": str(device.adb_path), "config": deepcopy(device.config),
                            "input_methods": int(device.input_methods), "screencap_methods": int(device.screencap_methods)}
        if isinstance(getattr(device, "name", None), str) and device.name.strip():
            target["device"]["name"] = device.name
        target["label"] = f"{getattr(device, 'name', address)} · {address}"
    if (preferences["adb_path"] and (target.get("device") or {}).get("adb_path")
            and Path(preferences["adb_path"]) != Path(target["device"]["adb_path"])):
        target = {"kind": "Adb", "key": address, "label": address}
    return {"target": target, "preferences": preferences, "emulator": target.pop("emulator", {})}


def adb_controller_options(connection):
    target = connection.get("target") or {}
    preferences = connection.get("preferences") or {}
    device = target.get("device") or {}
    path = preferences.get("adb_path") or device.get("adb_path", "")
    address = target.get("key")
    if not isinstance(address, str) or not address.strip():
        raise ValueError("실행할 ADB 기기 주소를 확인하세요.")
    if not path or not Path(path).is_file():
        raise ValueError("ADB 실행 파일 경로를 확인하세요.")
    if device.get("adb_path") and Path(path) != Path(device["adb_path"]):
        device = {}
    return {"adb_path": path, "address": address,
            **{key: deepcopy(device[key]) for key in ("screencap_methods", "input_methods", "config") if key in device}}


def adb_target_descriptor(connection):
    from app.monitoring import ConnectionTarget
    from maa.toolkit import AdbDevice
    target = connection["target"]
    metadata = target.get("device") or {}
    device = None
    if all(key in metadata for key in ("adb_path", "screencap_methods", "input_methods", "config")):
        device = AdbDevice(metadata.get("name") or target.get("label", target["key"]), Path(metadata["adb_path"]), target["key"],
                           metadata["screencap_methods"], metadata["input_methods"], deepcopy(metadata["config"]))
    return ConnectionTarget(target["key"], target.get("label", target["key"]), "Adb", device=device,
                            emulator=deepcopy(connection.get("emulator")) or None,
                            emulator_ambiguous=bool(target.get("_window_ambiguous")))


def minimize_adb_window(runtime):
    """Use a unique SDK-name binding, explicit selection, or exact launched PID."""
    from app.monitoring import window_process_info
    from app.runtime import SW_SHOWMINNOACTIVE
    bind_runtime_window(runtime)
    connection = getattr(runtime, "connection", None)
    if not isinstance(connection, dict):
        connection = getattr(runtime, "_adb_execution_connection", {})
    explicit = connection.get("emulator") or {}
    candidates = []
    if explicit:
        hwnd, expected_pid = explicit.get("hwnd"), explicit.get("pid")
        if type(hwnd) is not int or hwnd <= 0 or type(expected_pid) is not int or expected_pid <= 0:
            return False
        pid, path = window_process_info(hwnd)
        if pid != expected_pid or (explicit.get("path") and path.casefold() != explicit["path"].casefold()):
            return False
        candidates.append((hwnd, pid))
    elif getattr(runtime, "_adb_launcher_pid", None):
        for window in Toolkit.find_desktop_windows() or []:
            if not getattr(window, "window_name", ""):
                continue  # Ignore hidden/auxiliary IPC windows owned by the same process.
            hwnd = getattr(window.hwnd, "value", window.hwnd)
            pid, path = window_process_info(hwnd)
            if (pid == runtime._adb_launcher_pid and path
                    and Path(path) == runtime._adb_launcher_path):
                candidates.append((hwnd, pid))
    minimized = getattr(runtime, "_adb_minimized_windows", set())
    for hwnd, pid in candidates:
        identity = (hwnd, pid)
        if identity in minimized:
            continue  # Do not undo a user's later manual restore.
        runtime._user32.ShowWindow(hwnd, SW_SHOWMINNOACTIVE)
        if not runtime._user32.IsIconic(hwnd):
            return False
        minimized.add(identity)
    runtime._adb_minimized_windows = minimized
    if candidates and not getattr(runtime, "_adb_minimize_reported", False):
        runtime.log_sink._emit("앱플레이어 창을 최소화했습니다.")
        runtime._adb_minimize_reported = True
    elif not candidates and not getattr(runtime, "_adb_launcher_pid", None) and not getattr(runtime, "_adb_minimize_skipped", False):
        runtime.log_sink._emit("앱플레이어 최소화를 생략합니다. 제목이 일치하는 창을 하나로 특정하지 못했습니다. 인스턴스 설정에서 창을 직접 선택할 수 있습니다.")
        runtime._adb_minimize_skipped = True
    return True  # No window does not prevent Android input through ADB.


def launch_adb_program(runtime, settings, cancellation_requested=None, minimize=False):
    runtime._program_started_for_session = False
    runtime._adb_minimized_windows = set()
    runtime._adb_minimize_reported = False
    runtime._adb_minimize_skipped = False
    shortcut_process = None
    try:
        executable, arguments = parse_manual_program_command((settings or {}).get("manual_path"))
        arguments = settings.get("launch_arguments", arguments + parse_extra_arguments(settings.get("extra_arguments", "")))
        if cancellation_requested is not None and cancellation_requested():
            return False, "앱플레이어 자동 실행이 취소되었습니다."
        options = {}
        if minimize:
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 7  # SW_SHOWMINNOACTIVE, no foreground activation.
            options["startupinfo"] = startup
        if is_shortcut(executable):
            process = shortcut_process = launch_shortcut(executable, arguments, minimize=minimize)
        else:
            process = subprocess.Popen([str(executable), *arguments], cwd=str(executable.parent), shell=False,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **options)
        if shortcut_process is None and (type(process.pid) is not int or process.pid <= 0):
            return False, "앱플레이어 실행 프로세스를 확인하지 못했습니다."
        if process.poll() not in (None, 0):
            return False, "앱플레이어 실행 프로세스가 비정상 종료되었습니다."
        runtime._adb_launcher_pid = process.pid
        runtime._adb_launcher_path = process.executable_path if shortcut_process is not None else executable
        # A zero-exit launcher may legitimately hand off to another process.
        # Confirm the launched PID, wait for loading, then validate the actual
        # selected device through Controller.post_connection; do not guess a child.
        runtime._program_started_for_session = True
        wait_seconds = settings.get("adb_startup_wait_seconds", ADB_POST_PROCESS_WAIT_SECONDS)
        if not isinstance(wait_seconds, (int, float)) or isinstance(wait_seconds, bool) or not 0 <= wait_seconds <= 3600:
            wait_seconds = ADB_POST_PROCESS_WAIT_SECONDS
        runtime.log_sink._emit(f"앱플레이어 로딩을 기다립니다. {wait_seconds:g}초 후 다음 작업을 시작합니다.")
        deadline = time.monotonic() + wait_seconds
        next_minimize = 0.0
        while True:
            if cancellation_requested is not None and cancellation_requested():
                return False, "앱플레이어 자동 실행 후 대기가 취소되었습니다."
            exit_code = process.poll()
            if exit_code not in (None, 0):
                return False, f"앱플레이어 실행 프로세스가 비정상 종료되었습니다: {exit_code}"
            now = time.monotonic()
            if minimize and now >= next_minimize:
                if not minimize_adb_window(runtime):
                    return False, "앱플레이어의 선택된 창을 최소화하지 못했습니다."
                next_minimize = now + 0.5
            if now >= deadline:
                if minimize and not runtime._adb_minimized_windows:
                    runtime.log_sink._emit("앱플레이어 최소화 창을 확인하지 못했습니다. ADB 연결은 계속합니다.")
                return True, "앱플레이어 실행 후 대기를 완료했습니다."
            time.sleep(min(0.1, deadline - now))
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        return False, f"앱플레이어 자동 실행에 실패했습니다: {error}"
    finally:
        if shortcut_process is not None:
            shortcut_process.close()


def close_adb_player_for_retry(settings, context, cancellation_requested=None):
    """Normal close of one verified window/process only. No taskkill/vendor CLI."""
    from app.afterActions import WindowsAfterActionBackend, capture_window_target
    from app.monitoring import window_process_info
    from app.storageManagement import process_active
    try:
        executable, _arguments = parse_manual_program_command(settings.get("manual_path"))
        launcher_pid = context.get("launcher_pid")
        launcher_path = context.get("launcher_path")
        if not launcher_pid or not launcher_path:
            return False, "이 세션에서 실행한 앱플레이어 프로세스를 확인할 수 없어 자동 재시작을 생략합니다."
        if is_shortcut(executable):
            expected_path = Path(launcher_path)
        else:
            expected_path = executable
            if Path(launcher_path) != expected_path:
                return False, "설정한 경로와 실행 프로세스가 달라 자동 재시작을 생략합니다."
        explicit = (context.get("connection") or {}).get("emulator") or {}
        windows = ([explicit] if explicit else [
            {"hwnd": getattr(window.hwnd, "value", window.hwnd)}
            for window in Toolkit.find_desktop_windows() or [] if window.window_name])
        candidates = []
        for window in windows:
            hwnd = window.get("hwnd")
            pid, path = window_process_info(hwnd)
            if not pid or not path or Path(path) != expected_path:
                continue
            if (explicit and pid != explicit.get("pid")) or (launcher_pid and pid != launcher_pid):
                continue
            candidates.append(capture_window_target(hwnd))
        if len(candidates) != 1:
            return False, "재시작할 앱플레이어 창을 하나로 특정하지 못했습니다. 실제 실행 파일 경로를 확인하세요."
        target = candidates[0]
        if cancellation_requested and cancellation_requested():
            return False, "앱플레이어 재시작이 취소되었습니다."
        WindowsAfterActionBackend().close_program(target)
        deadline = time.monotonic() + 10
        while process_active(target.pid):
            if cancellation_requested and cancellation_requested():
                return False, "앱플레이어 재시작이 취소되었습니다."
            if time.monotonic() >= deadline:
                return False, "앱플레이어가 정상 종료 요청에 응답하지 않아 재시작을 중단했습니다. 강제 종료하지 않습니다."
            time.sleep(0.1)
        return True, "앱플레이어를 정상 종료했습니다."
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        return False, f"앱플레이어 재시작 준비 실패: {error}"
