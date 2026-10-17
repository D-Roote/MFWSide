"""Read-only storage inventory and narrowly scoped, opt-in generated-file cleanup."""
from dataclasses import dataclass, field
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import re
import stat
import time


LOG_SUFFIXES = {".log", ".dmp", ".mdmp"}
DEBUG_SUFFIXES = LOG_SUFFIXES | {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".mp4", ".avi"}


@dataclass(frozen=True)
class StorageGroup:
    key: str
    label: str
    base: Path
    root: Path
    kind: str = "debug"


@dataclass
class StorageUsage:
    group: StorageGroup
    bytes: int = 0
    files: int = 0
    removable_bytes: int = 0
    removable_files: int = 0
    checked_at_ns: int = field(default_factory=time.time_ns)
    errors: list = field(default_factory=list)


@dataclass
class CleanupResult:
    files: int = 0
    bytes: int = 0
    skipped: int = 0
    errors: list = field(default_factory=list)


def storage_groups(user_dir, root_dir, app_dir, resource_dir):
    """Never inventory/delete .git, virtualenvs, resources/models or saved accounts."""
    user, root, app, resources = map(lambda path: Path(path).absolute(), (user_dir, root_dir, app_dir, resource_dir))
    groups = [StorageGroup("sessions", "다중 실행 세션", user, user / "sessions", "sessions"),
              StorageGroup("debug", "앱 진단 로그·오류 이미지", user, user / "debug"),
              StorageGroup("user_logs", "사용자 폴더의 로그", user, user, "shallow")]
    if user.is_dir() and not user.is_symlink():
        for child in sorted(user.iterdir()):
            if child.name not in {"sessions", "debug", "config"} and child.is_dir() and not child.is_symlink():
                groups.append(StorageGroup("user_" + child.name, child.name + " 로그", user, child, "logs"))
    groups += [StorageGroup("resource_debug", "리소스 도구 진단 로그", resources, resources / "debug"),
               StorageGroup("legacy_debug", "이전 실행 진단 로그", root, root / "debug"),
               StorageGroup("legacy_logs", "이전 실행 로그", root, root / "logs", "logs"),
               StorageGroup("app_cache", "앱 Python 캐시", app, app, "cache"),
               StorageGroup("agent_cache", "Agent Python 캐시", root, root / "assets" / "agent", "cache"),
               StorageGroup("test_logs", "개발 테스트 진단 로그", root, root / ".cache" / "test_diagnostics", "logs"),
               StorageGroup("dev_logs", "개발 도구 로그", root, root / ".cache", "shallow")]
    return groups


def _is_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _safe_path(group, path):
    base, candidate = group.base.absolute(), Path(path).absolute()
    try:
        relative = candidate.relative_to(base)
    except ValueError:
        return False
    if ".." in relative.parts:
        return False
    for part in (base, *(base / Path(*relative.parts[:n]) for n in range(1, len(relative.parts) + 1))):
        try:
            if _is_reparse(part.lstat()):
                return False
        except FileNotFoundError:
            return True
    return candidate.resolve().is_relative_to(base.resolve())


def process_active(pid):
    """A recycled/inaccessible PID is protected conservatively, not terminated."""
    if pid == os.getpid():
        return True
    if os.name != "nt":
        return True  # Do not guess another platform's process identity.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    process = kernel.OpenProcess(0x00100000, False, pid)
    if not process:
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER: no such PID.
    try:
        return kernel.WaitForSingleObject(process, 0) != 0
    finally:
        kernel.CloseHandle(process)


def _active_runs(group):
    if group.kind != "sessions" or not group.root.is_dir():
        return []
    result, active = [group.root / "discovery"], {}
    for instance in group.root.iterdir():
        if not _safe_path(group, instance) or not instance.is_dir():
            continue
        for run in instance.iterdir():
            if not _safe_path(group, run) or not run.is_dir():
                continue
            for marker in run.glob("python_fault_*.log"):
                match = re.fullmatch(r"python_fault_(\d+)\.log", marker.name)
                if match:
                    pid = int(match[1])
                    if pid not in active:
                        active[pid] = process_active(pid)
                    if active[pid]:
                        result.append(run)
                        break
    return result


def _eligible(group, path):
    relative = path.relative_to(group.root)
    suffix = path.suffix.casefold()
    if group.kind == "cache":
        return "__pycache__" in relative.parts and suffix in {".pyc", ".pyo"}
    if group.kind in {"logs", "shallow"}:
        return suffix in LOG_SUFFIXES or ("debug" in relative.parts[:-1] and suffix in DEBUG_SUFFIXES)
    if group.kind == "sessions":
        return suffix in LOG_SUFFIXES or ("debug" in relative.parts[:-1] and suffix in DEBUG_SUFFIXES)
    return suffix in DEBUG_SUFFIXES


def _files(group, errors, cancelled):
    if not _safe_path(group, group.root):
        errors.append("연결된 폴더(심볼릭 링크/정션)는 조회·삭제하지 않습니다.")
        return
    if not group.root.exists():
        return
    def onerror(error):
        if len(errors) < 20:
            errors.append(str(error))
    for directory, subdirs, filenames in os.walk(group.root, followlinks=False, onerror=onerror):
        if cancelled():
            return
        current = Path(directory)
        subdirs[:] = [name for name in subdirs if _safe_path(group, current / name)]
        if group.kind == "shallow":
            subdirs.clear()
        for name in filenames:
            path = current / name
            if _safe_path(group, path) and _eligible(group, path):
                try:
                    yield path, path.stat(follow_symlinks=False)
                except OSError as error:
                    onerror(error)


def _protected(path, protected):
    return any(path == item or path.is_relative_to(item) for item in protected)


def scan_storage(groups, user_dir, cancelled=lambda: False):
    user = Path(user_dir).absolute()
    # The app's logger/fault descriptor remains open even while no task runs.
    protected = [user / "debug" / "maafw.log", user / "debug" / f"python_fault_{os.getpid()}.log"]
    result = []
    for group in groups:
        if cancelled():
            break
        usage = StorageUsage(group)
        try:
            live = [*protected, *_active_runs(group)]
            for path, info in _files(group, usage.errors, cancelled):
                usage.bytes += info.st_size
                usage.files += 1
                if not _protected(path, live):
                    usage.removable_bytes += info.st_size
                    usage.removable_files += 1
        except OSError as error:
            usage.errors.append(str(error))
        result.append(usage)
    return result


def cleanup_storage(usages, user_dir, cancelled=lambda: False):
    """Unlink only approved generated files, never recursively delete a directory."""
    result = CleanupResult()
    user = Path(user_dir).absolute()
    protected = [user / "debug" / "maafw.log", user / "debug" / f"python_fault_{os.getpid()}.log"]
    for usage in usages:
        if cancelled():
            break
        group = usage.group
        try:
            live = [*protected, *_active_runs(group)]
            for path, info in _files(group, result.errors, cancelled):
                if cancelled():
                    break
                if _protected(path, live) or info.st_mtime_ns > usage.checked_at_ns:
                    result.skipped += 1
                    continue
                try:
                    # Check again at the mutation boundary: no junction escape,
                    # replacement or append since this file was examined.
                    if not _safe_path(group, path) or info != path.stat(follow_symlinks=False):
                        result.skipped += 1
                        continue
                    path.unlink()
                    result.files += 1
                    result.bytes += info.st_size
                except OSError as error:
                    if len(result.errors) < 20:
                        result.errors.append(f"{path.name}: {error}")
        except OSError as error:
            result.errors.append(str(error))
    return result


def format_size(size):
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024


def reset_settings_files(user_dir, default_config):
    """Reset only the three application-owned config files; rollback on failure."""
    directory = Path(user_dir).absolute() / "config"
    scope = StorageGroup("config", "설정", Path(user_dir).absolute(), directory)
    if not _safe_path(scope, directory):
        raise OSError("설정 폴더가 외부 경로로 연결되어 초기화할 수 없습니다.")
    from app.settingsUI import SettingsStore
    payloads = {"maa_config.json": default_config, "user_config.json": {},
                "maa_option.json": {"logging": True, "save_draw": False, "save_on_error": True,
                                    "stdout_level": 2, "draw_quality": 85}}
    previous, written = {}, []
    for name in payloads:
        path = directory / name
        if not _safe_path(scope, path) or not _safe_path(scope, path.with_suffix(path.suffix + ".tmp")):
            raise OSError("설정 파일이 외부 경로로 연결되어 초기화할 수 없습니다.")
        previous[path] = path.read_bytes() if path.exists() else None
    try:
        for name, payload in payloads.items():
            path = directory / name
            SettingsStore._write_json(path, payload)
            written.append(path)
    except OSError:
        for path in written:
            if previous[path] is None:
                path.unlink(missing_ok=True)
            else:
                temp = path.with_suffix(".reset-rollback")
                temp.write_bytes(previous[path])
                temp.replace(path)
        raise
