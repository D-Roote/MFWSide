"""Headless generated-file cleanup after the exact owning process has exited."""
import ctypes
from ctypes import wintypes
import os
import re
import subprocess
import sys

from app.paths import PATHS
from app.storageManagement import (
    CleanupResult, StorageGroup, StorageUsage, _safe_path, cleanup_storage,
    process_active, storage_groups,
)


HELPER_FLAG = "--cleanup-after-exit"


def _process_api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return kernel


def schedule_exit_cleanup():
    """Inherit a process handle, never a PID or caller-provided deletion path."""
    kernel = _process_api()
    handle = kernel.OpenProcess(0x00100000, True, os.getpid())  # SYNCHRONIZE
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        arguments = [HELPER_FLAG, str(handle)]
        frozen = getattr(sys, "frozen", False)
        command = [sys.executable, *arguments] if frozen else [
            getattr(sys, "_base_executable", sys.executable), "-B",
            str(PATHS.root_dir / "main.py"), *arguments,
        ]
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        if frozen:
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, startupinfo=startup, close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW, env=environment,
            cwd=str(PATHS.root_dir),
        )
    finally:
        kernel.CloseHandle(handle)


def cleanup_generated_files(paths=PATHS):
    """Use the same allowlist as manual cleanup; never remove folders/resources."""
    user = paths.user_dir
    debug = StorageGroup("debug", "로그", user, user / "debug")
    if not _safe_path(debug, debug.root):
        return CleanupResult(errors=["로그 폴더가 외부 경로로 연결되어 있습니다."])
    try:
        for marker in debug.root.glob("python_fault_*.log"):
            if not _safe_path(debug, marker):
                continue
            match = re.fullmatch(r"python_fault_(\d+)\.log", marker.name)
            if match and process_active(int(match[1])):
                # A second GUI still owns this user directory. The last client
                # to close will retry; never clean another client's live data.
                return CleanupResult(skipped=1)
        groups = storage_groups(user, paths.root_dir, paths.app_dir, paths.resource_dir)
        # One pass: background cleanup does not need a size inventory first.
        usages = [StorageUsage(group) for group in groups]
        return cleanup_storage(usages, user, after_exit=True)
    except OSError as error:
        return CleanupResult(errors=[str(error)])


def run_cleanup_after_exit_cli(arguments):
    if len(arguments) != 2 or arguments[0] != HELPER_FLAG:
        return 2
    try:
        handle = int(arguments[1])
    except (TypeError, ValueError):
        return 2
    if not 0 < handle < 1 << (ctypes.sizeof(ctypes.c_void_p) * 8):
        return 2
    try:
        kernel = _process_api()
        try:
            if kernel.WaitForSingleObject(handle, 0xFFFFFFFF) != 0:
                return 1
        finally:
            kernel.CloseHandle(handle)
        return 1 if cleanup_generated_files().errors else 0
    except OSError:
        return 1
