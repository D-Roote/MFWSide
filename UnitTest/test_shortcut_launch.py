"""All Shell operations are mocked: never open a shortcut, URL, game or emulator."""
from contextlib import ExitStack
import ctypes
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import programLaunch
from app.adbStartup import launch_adb_program
from app.monitoring import MonitoringService
from app.pg_init import parse_manual_program_command, resolve_manual_program_path
from app.runtime import AppRuntime


class ShellLaunchTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.link = self.root / "한글 경로 & shortcut.lnk"
        self.link.touch()
        self.shell, self.kernel, self.ole = MagicMock(), MagicMock(), MagicMock()
        self.ole.CoInitializeEx.return_value = 0
        self.kernel.GetProcessId.return_value = 42
        self.kernel.CloseHandle.return_value = True
        self.kernel.WaitForSingleObject.return_value = 258
        self.kernel.QueryFullProcessImageNameW.side_effect = self.query_path
        self.shell.ShellExecuteExW.side_effect = self.execute
        self.load = self.stack.enter_context(patch("app.programLaunch._libraries", return_value=(self.shell, self.kernel, self.ole)))
        self.return_handle = 456
        self.requests = []

    def query_path(self, handle, flags, buffer, size):
        buffer.value = str(self.root / "actual process.exe")
        return True

    def execute(self, pointer):
        info = ctypes.cast(pointer, ctypes.POINTER(programLaunch.ShellExecuteInfo)).contents
        self.requests.append((info.lpFile, info.lpVerb, info.lpParameters, info.nShow, info.fMask))
        info.hProcess = self.return_handle
        return True

    def launch(self, *args, **kwargs):
        process = programLaunch.launch_shortcut(*args, **kwargs)
        self.addCleanup(process.close)
        return process

    def test_lnk_uses_windows_open_with_exact_path_and_no_command_shell(self):
        arguments = ["--profile", "two words", "&literal"]
        process = self.launch(self.link, arguments, minimize=True)
        self.assertEqual(self.requests, [(str(self.link.resolve()), "open", subprocess.list2cmdline(arguments), 7, 0x540)])
        self.assertEqual(process.pid, 42)
        self.assertEqual(process.executable_path, self.root / "actual process.exe")
        self.assertIsNone(process.poll())
        self.ole.CoUninitialize.assert_called_once()
        process.close()
        process.close()
        self.kernel.CloseHandle.assert_called_once_with(456)

    def test_url_is_opened_as_a_file_without_rewriting_its_protocol_or_content(self):
        shortcut = self.root / "Steam.url"
        shortcut.touch()
        self.launch(shortcut)
        self.assertEqual(self.requests[0][:4], (str(shortcut.resolve()), "open", None, 1))

    def test_shell_forward_to_existing_application_is_success_without_a_handle(self):
        self.return_handle = None
        process = self.launch(self.link)
        self.assertIsNone(process.pid)
        self.assertIsNone(process.executable_path)
        self.assertIsNone(process.poll())
        process.close()
        self.kernel.GetProcessId.assert_not_called()
        self.kernel.CloseHandle.assert_not_called()

    def test_process_path_query_failure_never_guesses_a_minimize_identity(self):
        self.kernel.QueryFullProcessImageNameW.side_effect = None
        self.kernel.QueryFullProcessImageNameW.return_value = False
        self.assertIsNone(self.launch(self.link).executable_path)

    def test_terminated_process_returns_even_still_active_numeric_exit_code(self):
        process = self.launch(self.link)
        self.kernel.WaitForSingleObject.return_value = 0
        def exit_code(handle, pointer):
            ctypes.cast(pointer, ctypes.POINTER(ctypes.wintypes.DWORD)).contents.value = 259
            return True
        self.kernel.GetExitCodeProcess.side_effect = exit_code
        self.assertEqual(process.poll(), 259)

    def test_wait_or_exit_query_failure_is_not_reported_as_running(self):
        process = self.launch(self.link)
        self.kernel.WaitForSingleObject.return_value = 0xffffffff
        with self.assertRaises(OSError):
            process.poll()
        self.kernel.WaitForSingleObject.return_value = 0
        self.kernel.GetExitCodeProcess.return_value = False
        with self.assertRaises(OSError):
            process.poll()

    def test_shell_failure_propagates_and_balances_com_initialization(self):
        self.shell.ShellExecuteExW.side_effect = None
        self.shell.ShellExecuteExW.return_value = False
        with self.assertRaises(OSError):
            programLaunch.launch_shortcut(self.link)
        self.ole.CoUninitialize.assert_called_once()

    def test_existing_com_apartment_is_not_uninitialized_by_us(self):
        self.ole.CoInitializeEx.return_value = -2147417850
        self.launch(self.link)
        self.ole.CoUninitialize.assert_not_called()

    def test_com_failure_never_requests_execution(self):
        self.ole.CoInitializeEx.return_value = -1
        with self.assertRaises(OSError):
            programLaunch.launch_shortcut(self.link)
        self.shell.ShellExecuteExW.assert_not_called()
        self.ole.CoUninitialize.assert_not_called()

    def test_missing_file_or_url_with_arguments_never_calls_shell(self):
        url = self.root / "Game.url"
        url.touch()
        for target, args in ((self.root / "missing.lnk", []), (url, ["--profile"])):
            with self.subTest(target=target), self.assertRaises(ValueError):
                programLaunch.launch_shortcut(target, args)
        self.load.assert_not_called()

    def test_no_extension_restriction_for_manual_files_but_missing_paths_still_fail(self):
        for name in ("Game.lnk", "Game.url", "app.com", "launcher.cmd", "no_extension"):
            path = self.root / name
            path.touch()
            self.assertEqual(resolve_manual_program_path(str(path)), path.resolve())
            self.assertEqual(parse_manual_program_command(f'"{path}"'), (path.resolve(), []))
        self.assertIsNone(resolve_manual_program_path(str(self.root / "missing.url")))


class ShortcutRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.link = self.root / "Game.lnk"
        self.link.touch()
        self.interface = {"controller": [{"name": "Win", "type": "Win32", "win32": {"window_regex": "^Game$"}}],
                          "resource": [{"path": ["."]}], "task": []}
        with patch("app.runtime.create_user32", return_value=MagicMock()), \
                patch.object(AppRuntime, "_load_interface", return_value=self.interface):
            self.runtime = AppRuntime()

    def test_win32_shortcut_uses_shell_then_closes_handle_without_stopping_process(self):
        with patch.object(self.runtime, "_find_target_window", return_value=(None, "not running")), \
                patch("app.runtime.launch_shortcut") as launch, patch("app.runtime.subprocess.Popen") as popen:
            self.assertTrue(self.runtime._launch_program({"resolved_path": str(self.link)})[0])
        popen.assert_not_called()
        launch.assert_called_once_with(self.link.resolve(), None)
        launch.return_value.close.assert_called_once()
        self.assertTrue(self.runtime._program_started_for_session)

    def test_existing_target_window_prevents_duplicate_shortcut_launch(self):
        with patch.object(self.runtime, "_find_target_window", return_value=(SimpleNamespace(hwnd=1), "running")), \
                patch("app.runtime.launch_shortcut") as launch:
            self.assertTrue(self.runtime._launch_program({"resolved_path": str(self.link)})[0])
        launch.assert_not_called()
        self.assertFalse(self.runtime._program_started_for_session)

    def test_shell_launch_failure_is_reported_without_claiming_session_started(self):
        with patch.object(self.runtime, "_find_target_window", return_value=(None, "not running")), \
                patch("app.runtime.launch_shortcut", side_effect=OSError("no association")):
            success, message = self.runtime._launch_program({"resolved_path": str(self.link)})
        self.assertFalse(success)
        self.assertIn("no association", message)
        self.assertFalse(self.runtime._program_started_for_session)

    def test_monitoring_keeps_controller_regex_but_does_not_compare_shortcut_to_exe(self):
        service = MonitoringService(self.interface, self.interface["resource"][0], self.root)
        self.addCleanup(service.close)
        windows = [SimpleNamespace(hwnd=1, window_name="Game", class_name="GameClass"),
                   SimpleNamespace(hwnd=2, window_name="Other", class_name="GameClass")]
        with patch.object(service, "_init_toolkit"), patch("app.monitoring.Toolkit.find_desktop_windows", return_value=windows), \
                patch("app.monitoring.window_process_info", return_value=(42, str(self.root / "ActualGame.exe"))):
            self.assertEqual(len(service.discover("Win", program_settings={"manual_path": str(self.link)})), 1)

    def test_adb_shortcut_waits_full_minute_even_when_shell_has_no_process_handle(self):
        process = MagicMock(pid=None, executable_path=None)
        process.poll.return_value = None
        runtime = SimpleNamespace(log_sink=MagicMock(), _user32=MagicMock(), connection={})
        clock = [0.0]
        with patch("app.adbStartup.launch_shortcut", return_value=process) as launch, \
                patch("app.adbStartup.time.monotonic", side_effect=lambda: clock[0]), \
                patch("app.adbStartup.time.sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                patch("app.adbStartup.subprocess.Popen") as popen:
            self.assertTrue(launch_adb_program(runtime, {"manual_path": f'"{self.link}"'})[0])
        self.assertGreaterEqual(clock[0], 60)
        self.assertIsNone(runtime._adb_launcher_pid)
        process.close.assert_called_once()
        process.terminate.assert_not_called()
        popen.assert_not_called()
        launch.assert_called_once_with(self.link.resolve(), [], minimize=False)

    def test_adb_shortcut_records_actual_process_path_not_shortcut_and_closes_on_cancel(self):
        process = MagicMock(pid=42, executable_path=self.root / "Player.exe")
        process.poll.return_value = None
        runtime = SimpleNamespace(log_sink=MagicMock(), _user32=MagicMock(), connection={})
        cancel = MagicMock(side_effect=[False, True])
        with patch("app.adbStartup.launch_shortcut", return_value=process):
            self.assertFalse(launch_adb_program(runtime, {"manual_path": f'"{self.link}"'}, cancel)[0])
        self.assertEqual(runtime._adb_launcher_path, self.root / "Player.exe")
        self.assertEqual(runtime._adb_launcher_pid, 42)
        process.close.assert_called_once()
        process.terminate.assert_not_called()
