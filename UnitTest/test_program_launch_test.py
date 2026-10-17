"""Launch-test UI and worker; all actual program/Shell execution is mocked."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from PySide6.QtCore import QThread
from PySide6.QtTest import QTest

from app.programTest import ProgramTestWorker, launch_program_test


class LaunchTestBackendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.target = self.root / "Game.exe"
        self.target.touch()

    def test_manual_command_preserves_arguments_without_shell_or_wait(self):
        with patch("app.programTest.subprocess.Popen") as launch, \
                patch("app.pg_init.find_steam_launch") as steam:
            message = launch_program_test({"resolved_path": str(self.target), "manual_path": "confirmed",
                                           "launch_arguments": ["--profile", "two words", "&literal"]})
        self.assertIn("수동 경로 실행 요청", message)
        self.assertEqual(launch.call_args.args[0], [str(self.target), "--profile", "two words", "&literal"])
        self.assertFalse(launch.call_args.kwargs["shell"])
        launch.return_value.wait.assert_not_called()
        steam.assert_not_called()

    def test_automatic_steam_folder_uses_existing_launch_command_builder(self):
        folder = self.root / "Installed Game"
        folder.mkdir()
        command = [str(self.target), "steam://run/123"]
        with patch("app.programTest.build_program_launch_command", return_value=(command, self.root)), \
                patch("app.programTest.subprocess.Popen") as launch:
            message = launch_program_test({"resolved_path": str(folder), "manual_path": ""})
        self.assertIn("자동 경로 실행 요청", message)
        self.assertEqual(launch.call_args.args[0], command)

    def test_shortcuts_use_same_shell_backend_and_close_only_the_handle(self):
        for extension in ("lnk", "url"):
            shortcut = self.root / f"Game.{extension}"
            shortcut.touch()
            with self.subTest(extension=extension), patch("app.programTest.launch_shortcut") as launch, \
                    patch("app.programTest.subprocess.Popen") as popen:
                launch_program_test({"resolved_path": str(shortcut), "manual_path": str(shortcut)})
                launch.assert_called_once_with(shortcut.resolve(), [])
                launch.return_value.close.assert_called_once()
                launch.return_value.terminate.assert_not_called()
                popen.assert_not_called()

    def test_missing_empty_deleted_or_unlaunchable_folder_never_starts_a_program(self):
        for path in ("", str(self.root / "missing.lnk"), str(self.root)):
            with self.subTest(path=path), patch("app.programTest.subprocess.Popen") as launch, \
                    patch("app.programTest.build_program_launch_command", return_value=([str(self.root)], self.root)), \
                    self.assertRaises(ValueError):
                launch_program_test({"resolved_path": path})
            launch.assert_not_called()

    def test_cancellation_checked_before_both_launch_backends(self):
        shortcut = self.root / "Game.lnk"
        shortcut.touch()
        for path in (self.target, shortcut):
            with self.subTest(path=path), patch("app.programTest.subprocess.Popen") as popen, \
                    patch("app.programTest.launch_shortcut") as shell, \
                    patch("app.pg_init.find_steam_launch", return_value=None), self.assertRaisesRegex(RuntimeError, "취소"):
                launch_program_test({"resolved_path": str(path)}, lambda: True)
            popen.assert_not_called()
            shell.assert_not_called()

    def test_worker_snapshots_settings_and_reports_launch_error(self):
        settings = {"resolved_path": str(self.target), "launch_arguments": ["--profile", "0"]}
        worker = ProgramTestWorker(settings)
        self.addCleanup(worker.deleteLater)
        settings["launch_arguments"][1] = "1"
        with patch("app.programTest.launch_program_test", side_effect=OSError("access denied")) as launch:
            worker.run()
        self.assertEqual(launch.call_args.args[0]["launch_arguments"], ["--profile", "0"])
        self.assertFalse(worker.result[0])
        self.assertIn("access denied", worker.result[1])


class LaunchTestUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window, self.panel = self.fixture.window, self.fixture.window.settings_panel
        self.target = self.window.runtime.user_dir / "Program.exe"
        self.target.touch()
        self.panel._confirmed_manual_path = ""
        self.panel.program_path_input.clear()
        self.panel._auto_program_path = self.target
        self.release = threading.Event()
        self.addCleanup(self.finish)

    def finish(self):
        self.release.set()
        worker = self.panel._program_test_worker
        if worker is not None:
            self.assertTrue(worker.wait(3000), "test worker did not finish")
        QTest.qWait(20)

    def begin_blocked(self):
        def launch(settings, cancelled):
            self.release.wait(2)
            return "테스트 실행 요청 완료"
        patcher = patch("app.programTest.launch_program_test", side_effect=launch)
        backend = patcher.start()
        self.addCleanup(patcher.stop)
        self.panel.program_test_button.click()
        return backend

    def test_controls_are_browse_confirm_test_and_input_is_smaller(self):
        controls = self.panel.program_path_input.parentWidget().layout()
        self.assertEqual([controls.itemAt(index).widget().text() for index in (1, 2, 3)], ["찾아보기", "확인", "테스트"])
        self.assertEqual(self.panel.program_path_input.minimumWidth(), 180)
        self.assertEqual(self.panel.program_test_button.accessibleName(), "프로그램 경로 실행 테스트")

    def test_automatic_path_is_snapshot_and_request_runs_outside_gui_thread(self):
        threads = []
        def launch(settings, cancelled):
            threads.append(QThread.currentThread())
            self.assertEqual(settings["resolved_path"], str(self.target))
            self.assertEqual(settings["manual_path"], "")
            return "자동 경로 실행 요청 완료"
        with patch("app.programTest.launch_program_test", side_effect=launch) as backend:
            self.panel.program_test_button.click()
            self.finish()
        backend.assert_called_once()
        self.assertNotEqual(threads[0], self.panel.thread())
        self.assertIn("자동 경로 실행 요청 완료", self.panel.program_active_status.text())
        self.assertFalse(self.panel.program_test_busy)

    def test_confirmed_manual_path_is_used_without_saving_or_changing_confirmation(self):
        self.panel.program_path_input.setText(f'"{self.target}" --profile "two words"')
        self.panel._apply_program_path()
        before = self.panel.store.config_path.read_bytes()
        with patch("app.programTest.launch_program_test", return_value="수동 경로 실행 요청 완료") as backend:
            self.panel.program_test_button.click()
            self.finish()
        settings = backend.call_args.args[0]
        self.assertEqual(settings["resolved_path"], str(self.target))
        self.assertEqual(settings["launch_arguments"], ["--profile", "two words"])
        self.assertEqual(self.panel.store.config_path.read_bytes(), before)
        self.assertEqual(self.panel.program_apply_button.text(), "초기화")

    def test_unconfirmed_manual_input_never_falls_back_to_auto_path(self):
        self.panel.program_path_input.setText('"Other.lnk"')
        with patch("app.settingsUI.ProgramTestWorker") as factory:
            self.panel._test_program_path()
        factory.assert_not_called()
        self.assertIn("확인한 뒤", self.panel.program_active_status.text())

    def test_missing_auto_path_shows_guidance_without_a_worker(self):
        self.panel._auto_program_path = None
        with patch("app.settingsUI.ProgramTestWorker") as factory:
            self.panel._test_program_path()
        factory.assert_not_called()
        self.assertIn("경로가 없습니다", self.panel.program_active_status.text())

    def test_busy_blocks_repeat_start_main_task_and_multi_start_until_finished(self):
        backend = self.begin_blocked()
        self.assertTrue(self.panel.program_test_busy)
        self.assertFalse(self.panel.program_test_button.isEnabled())
        self.assertFalse(self.panel.program_path_input.isEnabled())
        self.assertFalse(self.window.ui.workStartBtn.isEnabled())
        self.panel._test_program_path()
        with patch("app.winUI.RuntimeWorker") as runtime, patch.object(self.window.multi_panel.manager, "start") as multi:
            self.window.on_task_start()
            self.window.multi_panel.start_sessions([])
        runtime.assert_not_called()
        multi.assert_not_called()
        self.finish()
        backend.assert_called_once()
        self.assertTrue(self.panel.program_test_button.isEnabled())
        self.assertTrue(self.panel.program_path_input.isEnabled())

    def test_running_task_rejects_test_even_when_called_directly(self):
        self.window.isRunning = True
        try:
            self.window.check_start_button_state()
            self.assertFalse(self.panel.program_test_button.isEnabled())
            with patch("app.settingsUI.ProgramTestWorker") as factory:
                self.panel._test_program_path()
            factory.assert_not_called()
        finally:
            self.window.isRunning = False

    def test_worker_launch_error_is_visible_and_does_not_change_manual_path_validity(self):
        with patch("app.programTest.launch_program_test", side_effect=OSError("no association")):
            self.panel.program_test_button.click()
            self.finish()
        self.assertIn("no association", self.panel.program_active_status.text())
        self.assertFalse(self.panel._manual_path_error_active)
        self.assertFalse(self.panel.program_test_busy)

    def test_late_result_does_not_overwrite_changed_path(self):
        self.begin_blocked()
        self.panel.program_path_input.setText("changed manually by another control")
        self.finish()
        self.assertNotIn("테스트 실행 요청 완료", self.panel.program_active_status.text())

    def test_window_close_waits_for_test_worker_and_never_terminates_it(self):
        self.begin_blocked()
        worker = self.panel._program_test_worker
        self.window.close()
        self.assertTrue(self.window._close_pending)
        self.assertTrue(worker.isInterruptionRequested())
        self.finish()
        self.assertFalse(self.panel.program_test_busy)
        QTest.qWait(20)
        self.assertFalse(self.window._close_pending)

    def test_adb_test_uses_manual_path_without_connection_or_loading_wait(self):
        preset = {"name": "Adb", "type": "Adb"}
        self.panel.controllers.append(preset)
        self.panel.controller_combo.addItem("ADB", preset)
        self.panel.controller_combo.setCurrentIndex(self.panel.controller_combo.count() - 1)
        self.panel.program_path_input.setText(f'"{self.target}" -v 0')
        self.panel._apply_program_path()
        with patch("app.programTest.launch_program_test", return_value="수동 경로 실행 요청 완료") as backend, \
                patch("app.adbStartup.launch_adb_program") as adb:
            self.panel.program_test_button.click()
            self.finish()
        self.assertEqual(backend.call_args.args[0]["launch_arguments"], ["-v", "0"])
        adb.assert_not_called()

    def test_three_buttons_fit_minimum_window_and_both_themes(self):
        self.window.show()
        self.window.ui.mainPages.setCurrentWidget(self.window.ui.settingTab)
        for theme in ("light", "dark"):
            self.panel.theme_combo.setCurrentIndex(self.panel.theme_combo.findData(theme))
            for width in (self.window.minimumWidth(), 1400):
                with self.subTest(theme=theme, width=width):
                    self.window.resize(width, 850)
                    QTest.qWait(20)
                    row = self.panel.program_path_row
                    widgets = [self.panel.program_path_input, self.panel.program_browse_button,
                               self.panel.program_apply_button, self.panel.program_test_button]
                    for left, right in zip(widgets, widgets[1:]):
                        self.assertLess(left.geometry().right(), right.geometry().left())
                    self.assertGreaterEqual(widgets[0].width(), 180)
                    self.assertLessEqual(widgets[0].fontMetrics().horizontalAdvance(widgets[0].placeholderText()),
                                         widgets[0].width() - 22)
                    self.assertEqual(widgets[-1].geometry().right(), widgets[-1].parentWidget().contentsRect().right())
                    self.assertLessEqual(row.description_label.heightForWidth(row.description_label.width()),
                                         2 * row.description_label.fontMetrics().lineSpacing())
                    self.assertEqual(self.panel.detail_scroll.horizontalScrollBar().maximum(), 0)
