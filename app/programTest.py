"""Explicit launch-only test: no Tasker, controller, minimize or boot wait."""
from copy import deepcopy
from pathlib import Path
import subprocess

from PySide6.QtCore import QThread

from app.pg_init import build_program_launch_command
from app.programLaunch import is_shortcut, launch_shortcut


def request_program_launch(settings, cancellation_requested=None, *, minimize=False):
    path = settings.get("resolved_path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("실행할 경로가 없습니다. 자동 검색 결과 또는 수동 경로를 확인하세요.")
    target = Path(path).resolve()
    if not target.is_file() and not target.is_dir():
        raise ValueError("실행할 파일 또는 폴더를 찾을 수 없습니다.")
    arguments = settings.get("launch_arguments") or []
    if is_shortcut(target):
        if cancellation_requested is not None and cancellation_requested():
            raise RuntimeError("실행 테스트가 취소되었습니다.")
        process = launch_shortcut(target, arguments, **({"minimize": True} if minimize else {}))
        identity = ({"pid": process.pid, "path": str(process.executable_path)}
                    if type(process.pid) is int and process.pid > 0 and process.executable_path else None)
        process.close()
    else:
        command, directory = build_program_launch_command(target, arguments)
        if target.is_dir() and (len(command) != 2 or not command[1].startswith("steam://run/")):
            raise ValueError("폴더의 Steam 실행 정보를 확인할 수 없습니다. 실행 파일을 직접 지정하세요.")
        if cancellation_requested is not None and cancellation_requested():
            raise RuntimeError("실행 테스트가 취소되었습니다.")
        options = {}
        if minimize:
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 7
            options["startupinfo"] = startup
        process = subprocess.Popen(command, cwd=str(directory), shell=False, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), **options)
        identity = {"pid": process.pid, "path": str(Path(command[0]).resolve())} if type(process.pid) is int and process.pid > 0 else None
    return identity


def launch_program_test(settings, cancellation_requested=None):
    request_program_launch(settings, cancellation_requested)
    source = "수동" if settings.get("manual_path") else "자동"
    return f"{source} 경로 실행 요청을 보냈습니다. 대상 프로그램이 열렸는지 확인하세요."


class ProgramTestWorker(QThread):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = deepcopy(settings)
        self.result = (False, "실행 테스트를 완료하지 못했습니다.")

    def run(self):
        try:
            self.result = (True, launch_program_test(self.settings, self.isInterruptionRequested))
        except Exception as error:
            self.result = (False, f"실행 테스트 실패: {error}")
