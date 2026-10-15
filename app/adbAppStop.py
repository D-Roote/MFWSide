"""Generic, device-scoped Android app stopping; never shuts down the app player."""
from dataclasses import dataclass, replace
from pathlib import Path
import re
import subprocess
import time
from uuid import UUID


PACKAGE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
SERIAL = re.compile(r"[A-Za-z0-9_.:\[\]%-]+")


@dataclass(frozen=True)
class AdbAppStopTarget:
    adb_path: str
    serial: str
    boot_id: str
    user_id: str


class AdbAppStopBackend:
    """Use public ADB commands only, no vendor APIs, root, wipe or host shutdown."""
    def __init__(self, cancellation_requested=None):
        self.cancelled = cancellation_requested or (lambda: False)
        self.deadline = time.monotonic() + 90

    def _adb(self, target, *arguments):
        if (not isinstance(target, AdbAppStopTarget) or not isinstance(target.serial, str)
                or not SERIAL.fullmatch(target.serial) or target.serial.startswith("-")
                or not isinstance(target.user_id, str) or not re.fullmatch(r"\d{1,6}", target.user_id)):
            raise RuntimeError("유효한 ADB 앱 종료 대상 정보가 없습니다.")
        if self.cancelled():
            raise RuntimeError("완료 후 앱 종료 요청이 취소되었습니다.")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("완료 후 앱 종료 요청의 제한 시간을 초과했습니다.")
        if not isinstance(target.adb_path, str) or not Path(target.adb_path).is_file():
            raise RuntimeError("작업에 사용한 ADB 실행 파일이 없거나 변경되었습니다.")
        try:
            result = subprocess.run(
                [target.adb_path, "-s", target.serial, *arguments],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, timeout=min(8, remaining), encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"ADB 앱 종료 명령을 처리하지 못했습니다: {error}") from error
        output = result.stdout.strip().lstrip("\ufeff")
        error_text = (result.stderr or "").strip()
        if (result.returncode or re.search(r"(?:^|\n)(?:Error:|Exception|Security exception|error:)",
                                          output + "\n" + error_text, re.I)):
            raise RuntimeError((error_text or output or "ADB 명령 실패")[:300])
        return output

    def _identity(self, target):
        try:
            boot_id = str(UUID(self._adb(target, "shell", "cat", "/proc/sys/kernel/random/boot_id")))
        except (ValueError, TypeError) as error:
            raise RuntimeError("ADB 기기의 부팅 정보를 확인하지 못했습니다.") from error
        user = self._adb(target, "shell", "am", "get-current-user")
        if not re.fullmatch(r"\d{1,6}", user):
            raise RuntimeError("ADB 기기의 현재 사용자 정보를 확인하지 못했습니다.")
        return boot_id, user

    def capture(self, connection):
        from app.adbStartup import adb_controller_options
        options = adb_controller_options(connection)
        target = AdbAppStopTarget(str(Path(options["adb_path"]).resolve()), options["address"], "", "0")
        boot_id, user = self._identity(target)
        return replace(target, boot_id=boot_id, user_id=user)

    def _verify_device(self, target):
        if self._identity(target) != (target.boot_id, target.user_id):
            raise RuntimeError("ADB 기기 또는 사용자가 변경되어 앱 종료 요청을 생략합니다.")

    @staticmethod
    def _packages(output):
        packages = set()
        for line in output.splitlines():
            package = line.removeprefix("package:").strip()
            if not line.startswith("package:") or not PACKAGE.fullmatch(package):
                raise RuntimeError("사용자 앱 목록이 올바르지 않아 앱 종료 요청을 생략합니다.")
            packages.add(package)
        return packages

    def stop(self, target):
        self._verify_device(target)
        user = target.user_id
        packages = self._packages(self._adb(target, "shell", "pm", "list", "packages", "-3", "--user", user))
        if not packages:
            return "완료 후 종료할 사용자 설치 앱이 없습니다."
        # The framework may itself be using a third-party launcher/input method.
        home = self._adb(target, "shell", "cmd", "package", "resolve-activity", "--brief", "--user", user,
                         "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME")
        homes = {line.strip().split("/", 1)[0] for line in home.splitlines() if "/" in line}
        if not homes or not all(PACKAGE.fullmatch(package) for package in homes):
            raise RuntimeError("기기의 홈 앱을 확인하지 못해 앱 일괄 종료를 생략합니다.")
        packages.difference_update(homes)
        ime = self._adb(target, "shell", "ime", "list", "-s")
        for line in ime.splitlines():
            if not line.strip():
                continue
            package, separator, _component = line.strip().partition("/")
            if not separator or not PACKAGE.fullmatch(package):
                raise RuntimeError("입력기 앱을 확인하지 못해 앱 일괄 종료를 생략합니다.")
            packages.discard(package)
        if len(packages) > 128:
            raise RuntimeError("종료할 앱이 너무 많아 앱 일괄 종료를 생략합니다.")
        completed = 0
        try:
            for package in sorted(packages):
                # Reject port reuse, reboots and Android-user switches between requests.
                self._verify_device(target)
                output = self._adb(target, "shell", "am", "force-stop", "--user", user, package)
                if output:
                    raise RuntimeError(f"앱 종료 응답을 확인하지 못했습니다: {output[:200]}")
                completed += 1
        except RuntimeError as error:
            raise RuntimeError(f"사용자 설치 앱 {completed}개 종료 후 중단했습니다. {error}") from error
        return f"완료 후 사용자 설치 앱 {completed}개를 종료했습니다. 시스템·홈·입력기 앱은 제외했습니다."


def capture_adb_app_stop_target(connection, cancellation_requested=None):
    return AdbAppStopBackend(cancellation_requested).capture(connection)
