"""Daily restart primitives. Never guess an Android package or terminate a player."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import ctypes
from ctypes import wintypes
import re
import time

from app.adbAppStop import AdbAppStopBackend, PACKAGE
from app.adbStartup import prepare_adb_connection
from app.afterActions import WindowsAfterActionBackend, capture_window_target
from app.monitoring import MonitoringService
from app.programTest import request_program_launch
from app.runtime import PROGRAM_LAUNCH_ENTRY, PROGRAM_POST_WINDOW_WAIT_SECONDS
from app.sessionRuntime import serialize_target


def next_daily_restart(now, clock):
    """Local wall-clock schedule, including the requested one-minute offset."""
    hour, minute = map(int, clock.split(":"))
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(minutes=1)
    if due <= now:
        due += timedelta(days=1)
    return due


def check_cancelled(cancelled):
    if cancelled():
        raise RuntimeError("일일 초기화 재시작이 취소되었습니다.")


def wait_seconds(seconds, cancelled):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        check_cancelled(cancelled)
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    check_cancelled(cancelled)


def restart_launch_entry(resource, queue):
    """Read SDK-normalized nodes, including defaults/overlays, not raw filenames."""
    for entry, override in queue:
        if entry != PROGRAM_LAUNCH_ENTRY and override and not resource.override_pipeline(override):
            raise RuntimeError("일일 재시작의 작업 오버라이드를 적용하지 못했습니다.")
    entries = set()
    for name in resource.node_list:
        node = resource.get_node_data(name) or {}
        action = node.get("action", {})
        if node.get("enabled") is False or not isinstance(action, dict) or action.get("type") != "StartApp":
            continue
        entry = action.get("param", {}).get("package", "")
        package, _, activity = entry.partition("/") if isinstance(entry, str) else ("", "", "")
        if PACKAGE.fullmatch(package) and (not activity or re.fullmatch(r"[A-Za-z0-9_.$]+", activity)):
            entries.add(entry)
    packages = {entry.split("/", 1)[0] for entry in entries}
    if len(packages) != 1:
        raise RuntimeError("일일 재시작 대상 앱을 특정할 수 없습니다. 파이프라인의 StartApp package를 확인하세요.")
    components = {entry for entry in entries if "/" in entry}
    if len(components) > 1:
        raise RuntimeError("일일 재시작 대상 앱의 실행 Activity가 여러 개입니다.")
    return next(iter(components or entries))


def load_restart_launch_entry(snapshot, cancelled):
    from maa.resource import Resource
    resource = Resource()
    directory = Path(snapshot["interface_path"]).resolve().parent
    for path in snapshot["resource"]["path"]:
        check_cancelled(cancelled)
        if not resource.post_bundle(str((directory / path).resolve())).wait().succeeded:
            raise RuntimeError("일일 재시작 대상 앱의 리소스를 읽지 못했습니다.")
    check_cancelled(cancelled)
    return restart_launch_entry(resource, snapshot["queue"])


def restart_android_app(snapshot, connection, cancelled):
    entry = load_restart_launch_entry(snapshot, cancelled)
    package, _, activity = entry.partition("/")
    connection = deepcopy(connection) if connection.get("target") else prepare_adb_connection(
        snapshot["program"].get("connection", {}))
    backend = AdbAppStopBackend(cancelled)
    target = backend.capture(connection)
    # Resolve the launch Activity before any destructive operation.
    if not activity:
        output = backend._adb(target, "shell", "cmd", "package", "resolve-activity", "--brief",
                              "--user", target.user_id, "-a", "android.intent.action.MAIN",
                              "-c", "android.intent.category.LAUNCHER", package)
        components = [line.strip() for line in output.splitlines()
                      if re.fullmatch(re.escape(package) + r"/[A-Za-z0-9_.$]+", line.strip())]
        if len(components) != 1:
            raise RuntimeError("일일 재시작 대상 앱의 실행 Activity를 확인하지 못했습니다.")
        entry = components[0]
    backend._verify_device(target)
    backend._adb(target, "shell", "am", "force-stop", "--user", target.user_id, package)
    backend._verify_device(target)
    check_cancelled(cancelled)
    output = backend._adb(target, "shell", "am", "start", "-W", "--user", target.user_id, "-n", entry)
    if "Status: ok" not in output:
        raise RuntimeError("일일 재시작 대상 앱의 실행을 확인하지 못했습니다.")
    wait_seconds(PROGRAM_POST_WINDOW_WAIT_SECONDS, cancelled)
    return connection


def close_window_and_wait(hwnd, cancelled):
    """Wait on the exact process handle; no taskkill, name matching or PID reuse."""
    target = capture_window_target(hwnd)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, target.pid)
    if not handle:
        raise RuntimeError("일일 재시작 대상 프로세스의 종료를 확인할 권한이 없습니다.")
    try:
        check_cancelled(cancelled)
        WindowsAfterActionBackend().close_program(target)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            check_cancelled(cancelled)
            status = kernel.WaitForSingleObject(handle, 100)
            if status == 0:
                return
            if status != 258:
                raise RuntimeError("대상 프로세스의 종료 확인에 실패했습니다.")
        raise RuntimeError("대상 프로그램이 종료 요청에 응답하지 않아 재시작을 중단했습니다.")
    finally:
        kernel.CloseHandle(handle)


def restart_windows_program(snapshot, connection, cancelled):
    service = MonitoringService(snapshot["interface"], snapshot["resource"], snapshot["user_dir"])
    preset = connection.get("preset") or snapshot["controller"]["name"]
    targets = service.discover(preset, program_settings=snapshot["program"])
    expected = connection.get("target") or {}
    if expected:
        matching = [target for target in targets if target.hwnd == expected.get("hwnd") and target.pid == expected.get("pid")]
        if targets and len(matching) != 1:
            raise RuntimeError("일일 재시작 대상 창이 변경되었습니다. 다른 창은 종료하지 않습니다.")
        targets = matching
    if len(targets) > 1:
        raise RuntimeError("일일 재시작 대상 창이 여러 개입니다. 연결 대상을 먼저 선택하세요.")
    if targets:
        close_window_and_wait(targets[0].hwnd, cancelled)
    check_cancelled(cancelled)
    request_program_launch(snapshot["program"], cancelled, minimize=snapshot.get("minimize", False))
    wait_seconds(PROGRAM_POST_WINDOW_WAIT_SECONDS, cancelled)
    targets = service.discover(preset, program_settings=snapshot["program"])
    if len(targets) != 1:
        raise RuntimeError("재실행한 대상 창을 하나로 특정하지 못했습니다. 연결 상태를 확인하세요.")
    return {**connection, "preset": preset, "target": serialize_target(targets[0])}


def restart_target(snapshot, connection, cancelled):
    check_cancelled(cancelled)
    if not snapshot["program"].get("resolved_path"):
        raise RuntimeError("프로그램 실행 경로가 없어 일일 재시작을 생략했습니다.")
    preset = connection.get("preset") or snapshot["controller"].get("name")
    controller = next((item for item in snapshot["interface"].get("controller", []) if item.get("name") == preset), {})
    if controller.get("type") == "Adb":
        return restart_android_app(snapshot, connection, cancelled)
    if controller.get("type") == "Win32":
        return restart_windows_program(snapshot, connection, cancelled)
    raise RuntimeError("일일 재시작에 사용할 컨트롤러를 확인하세요.")
