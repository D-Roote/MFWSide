"""Generic ADB app stopping with fully mocked subprocesses; never touches a device."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_runtime_lifecycle as fixtures
from app.adbAppStop import AdbAppStopBackend, AdbAppStopTarget


BOOT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_BOOT = "22222222-2222-4222-8222-222222222222"


class FakeAdb:
    def __init__(self):
        self.boot_id, self.user_id = BOOT_ID, "10"
        self.packages = "package:com.example.game\npackage:com.example.helper\npackage:com.example.home\npackage:com.example.ime"
        self.home = "com.example.home/.Home"
        self.ime = "com.example.ime/.InputMethod"
        self.calls, self.stops = [], []
        self.on_stop = None
        self.error = None
        self.stop_output = ""

    def __call__(self, command, **options):
        self.calls.append((list(command), options))
        if self.error:
            raise self.error
        args = command[3:]
        if args == ["shell", "cat", "/proc/sys/kernel/random/boot_id"]:
            output = self.boot_id
        elif args == ["shell", "am", "get-current-user"]:
            output = self.user_id
        elif args[:5] == ["shell", "pm", "list", "packages", "-3"]:
            output = self.packages
        elif args[:4] == ["shell", "cmd", "package", "resolve-activity"]:
            output = self.home
        elif args == ["shell", "ime", "list", "-s"]:
            output = self.ime
        elif args[:3] == ["shell", "am", "force-stop"]:
            self.stops.append(args[-1])
            if self.on_stop:
                self.on_stop()
            output = self.stop_output
        else:
            raise AssertionError(f"Unexpected command: {command}")
        return SimpleNamespace(returncode=0, stdout=output, stderr="")


class AdbAppStopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.adb = Path(self.directory.name) / "adb.exe"
        self.adb.touch()
        self.target = AdbAppStopTarget(str(self.adb), "usb-device-123", BOOT_ID, "10")
        self.connection = {"target": {"kind": "Adb", "key": self.target.serial},
                           "preferences": {"adb_path": str(self.adb)}}
        self.fake = FakeAdb()
        patcher = patch("app.adbAppStop.subprocess.run", side_effect=self.fake)
        self.command_run = patcher.start()
        self.addCleanup(patcher.stop)
        self.backend = AdbAppStopBackend()

    def test_capture_preserves_actual_connection_and_is_json_serializable(self):
        target = self.backend.capture(self.connection)
        self.assertEqual(target, self.target)
        self.assertEqual(AdbAppStopTarget(**json.loads(json.dumps(asdict(target)))), target)
        self.assertEqual(len(self.fake.calls), 2)

    def test_user_apps_only_and_all_commands_select_exact_device_without_shell(self):
        message = self.backend.stop(self.target)
        self.assertEqual(self.fake.stops, ["com.example.game", "com.example.helper"])
        self.assertIn("2개", message)
        for command, options in self.fake.calls:
            self.assertEqual(command[:3], [str(self.adb), "-s", "usb-device-123"])
            self.assertIs(options["shell"], False)
            self.assertLessEqual(options["timeout"], 8)
            self.assertFalse(set(command) & {"kill-server", "uninstall", "clear", "reboot", "emu", "su", "taskkill"})
        self.assertTrue(any(command[3:] == ["shell", "pm", "list", "packages", "-3", "--user", "10"]
                            for command, _ in self.fake.calls))

    def test_no_user_apps_never_requests_home_or_stop(self):
        self.fake.packages = ""
        self.assertIn("없습니다", self.backend.stop(self.target))
        self.assertEqual(len(self.fake.calls), 3)
        self.assertFalse(self.fake.stops)

    def test_home_and_input_method_are_not_stopped_even_when_user_installed(self):
        self.backend.stop(self.target)
        self.assertNotIn("com.example.home", self.fake.stops)
        self.assertNotIn("com.example.ime", self.fake.stops)

    def test_reboot_or_android_user_change_prevents_every_stop(self):
        for boot, user in ((OTHER_BOOT, "10"), (BOOT_ID, "11")):
            with self.subTest(boot=boot, user=user):
                self.fake.boot_id, self.fake.user_id = boot, user
                with self.assertRaisesRegex(RuntimeError, "변경되어"):
                    self.backend.stop(self.target)
        self.assertFalse(self.fake.stops)

    def test_identity_is_revalidated_between_each_app_stop(self):
        self.fake.on_stop = lambda: setattr(self.fake, "boot_id", OTHER_BOOT)
        with self.assertRaisesRegex(RuntimeError, "1개 종료 후 중단"):
            self.backend.stop(self.target)
        self.assertEqual(self.fake.stops, ["com.example.game"])

    def test_malformed_package_list_never_reaches_a_stop_command(self):
        for packages in ("not a package", "package:com.example.game;reboot", "package:--help"):
            with self.subTest(packages=packages):
                self.fake.packages = packages
                with self.assertRaisesRegex(RuntimeError, "목록이 올바르지"):
                    self.backend.stop(self.target)
        self.assertFalse(self.fake.stops)

    def test_unresolved_home_or_input_methods_fail_closed(self):
        self.fake.home = "No activity found"
        with self.assertRaisesRegex(RuntimeError, "홈 앱"):
            self.backend.stop(self.target)
        self.fake.home, self.fake.ime = "com.example.home/.Home", "Unknown command: list"
        with self.assertRaisesRegex(RuntimeError, "입력기"):
            self.backend.stop(self.target)
        self.assertFalse(self.fake.stops)

    def test_invalid_serial_user_and_missing_executable_do_not_spawn(self):
        for target in (replace(self.target, serial="-d"), replace(self.target, serial="usb;reboot"),
                       replace(self.target, user_id="all"), replace(self.target, adb_path=str(self.adb) + ".missing")):
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                self.backend.stop(target)
        self.command_run.assert_not_called()

    def test_invalid_boot_id_and_user_cannot_be_captured(self):
        self.fake.boot_id = "permission denied"
        with self.assertRaisesRegex(RuntimeError, "부팅 정보"):
            self.backend.capture(self.connection)
        self.fake.boot_id, self.fake.user_id = BOOT_ID, "unknown"
        with self.assertRaisesRegex(RuntimeError, "사용자 정보"):
            self.backend.capture(self.connection)

    def test_command_errors_and_stderr_security_errors_are_not_success(self):
        for result in (SimpleNamespace(returncode=1, stdout="", stderr="device offline"),
                       SimpleNamespace(returncode=0, stdout="Error: denied", stderr=""),
                       SimpleNamespace(returncode=0, stdout="", stderr="Security exception: denied")):
            with self.subTest(result=result):
                self.command_run.side_effect = None
                self.command_run.return_value = result
                with self.assertRaises(RuntimeError):
                    self.backend.stop(self.target)
        self.assertFalse(self.fake.stops)

    def test_timeout_is_reported_without_any_kill_or_retry(self):
        self.fake.error = subprocess.TimeoutExpired("adb", 8)
        with self.assertRaisesRegex(RuntimeError, "처리하지 못했습니다"):
            self.backend.stop(self.target)
        self.assertEqual(self.command_run.call_count, 1)

    def test_cancellation_before_and_between_stops_prevents_further_commands(self):
        event = threading.Event()
        backend = AdbAppStopBackend(event.is_set)
        event.set()
        with self.assertRaisesRegex(RuntimeError, "취소"):
            backend.stop(self.target)
        self.command_run.assert_not_called()
        event.clear()
        self.fake.on_stop = event.set
        with self.assertRaisesRegex(RuntimeError, "1개 종료 후 중단"):
            backend.stop(self.target)
        self.assertEqual(self.fake.stops, ["com.example.game"])

    def test_expired_total_deadline_and_large_batches_are_bounded(self):
        self.backend.deadline = time.monotonic() - 1
        with self.assertRaisesRegex(RuntimeError, "제한 시간"):
            self.backend.stop(self.target)
        self.command_run.assert_not_called()
        self.backend.deadline = time.monotonic() + 90
        self.fake.packages = "\n".join(f"package:com.example.app{index}" for index in range(129))
        with self.assertRaisesRegex(RuntimeError, "너무 많아"):
            self.backend.stop(self.target)
        self.assertFalse(self.fake.stops)

    def test_unexpected_stop_response_is_not_reported_as_success(self):
        self.fake.stop_output = "unknown response"
        with self.assertRaisesRegex(RuntimeError, "0개 종료 후 중단"):
            self.backend.stop(self.target)
        self.assertEqual(len(self.fake.stops), 1)


if __name__ == "__main__":
    unittest.main()
