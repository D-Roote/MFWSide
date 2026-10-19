"""Startup preferences and this installation's current-user Windows Run entry."""
from hashlib import sha256
from pathlib import Path
import os
import re
import subprocess
import sys


DEFAULT_STARTUP_CONFIG = {
    "launch_on_login": False,
    "minimize_app": False,
    "auto_start_tasks": False,
    "launch_program": False,
    "restart_on_connection_failure": False,
    "emulator_wait_seconds": 60,
    "task_wait_seconds": 0,
    "daily_restart": False,
    "daily_restart_time": "00:00",
}


def normalize_startup_config(raw):
    raw = raw if isinstance(raw, dict) else {}
    result = dict(DEFAULT_STARTUP_CONFIG)
    for key, default in result.items():
        value = raw.get(key)
        if isinstance(default, bool):
            result[key] = value if isinstance(value, bool) else default
        elif isinstance(default, str):
            if isinstance(value, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                result[key] = value
        elif type(value) is int and 0 <= value <= 3600:
            result[key] = value
    return result


class WindowsStartupRegistration:
    RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def __init__(self, root_dir):
        self.root = Path(root_dir).resolve()
        digest = sha256(os.path.normcase(str(self.root)).encode("utf-8")).hexdigest()[:12]
        self.value_name = "MAABA_" + digest

    def command(self):
        executable = Path(sys.executable).resolve()
        if getattr(sys, "frozen", False):
            arguments = [str(executable)]
        else:
            main = self.root / "main.py"
            if not main.is_file():
                raise OSError("앱 진입점을 찾지 못해 시스템 시작 등록을 할 수 없습니다.")
            pythonw = executable.with_name("pythonw.exe")
            arguments = [str(pythonw if pythonw.is_file() else executable), str(main)]
        command = subprocess.list2cmdline(arguments)
        if len(command) > 260 or any(char in command for char in ("\0", "\r", "\n")):
            raise ValueError("시스템 시작 명령이 Windows Run 등록 한도(260자)를 초과하거나 형식이 올바르지 않습니다.")
        return command

    def is_enabled(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0, winreg.KEY_READ) as key:
                value, _kind = winreg.QueryValueEx(key, self.value_name)
        except FileNotFoundError:
            return False
        return value == self.command()

    def set_enabled(self, enabled):
        """Called only after an explicit toggle/reset, never at app import/start."""
        if os.name != "nt":
            raise OSError("시스템 시작 실행은 Windows에서만 지원합니다.")
        import winreg
        if enabled:
            command = self.command()
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, self.value_name, 0, winreg.REG_SZ, command)
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, self.value_name)
            except FileNotFoundError:
                pass
