"""Task-start minimization belongs to the host UI, not its target controller."""
from copy import deepcopy
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from test_multi_execution import FakeContext, connection
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QLabel
from app.settingsUI import DEFAULT_MAA_CONFIG, SettingsStore
from app.storageManagement import reset_settings_files


class TaskAppMinimizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.panel = self.window.settings_panel

    def enable_app_minimize(self):
        self.panel.task_minimize_app_checkbox.setChecked(True)

    def start_mock_task(self, worker=None):
        worker = worker or MagicMock(succeeded=True, result_message="done")
        with patch("app.winUI.RuntimeWorker", return_value=worker) as factory:
            self.window.ui.workStartBtn.click()
        return worker, factory

    def prepare_instances(self):
        window = self.window
        window.runtime.interface_path = window.runtime.user_dir / "interface.json"
        manager = window.multi_panel.manager
        manager.context = FakeContext()
        manager.desktop_lock = manager.context.Lock()
        self.addCleanup(manager.timer.stop)

        def discard_fake_processes():
            for session in manager.sessions.values():
                session.process = None

        self.addCleanup(discard_fake_processes)
        ids = []
        for value in (42, 43):
            data = connection(value=value)
            data["preset"] = "Win32PrintWindow"
            ids.append(manager.add(data))
        return ids

    def test_setting_follows_target_minimize_without_renaming_start_tab(self):
        section = self.panel.task_minimize_app_row.parentWidget()
        titles = [label.text() for label in section.findChildren(QLabel, "settingsRowTitle")]
        self.assertEqual(titles[:2], ["작업 실행 시 최소화", "작업 실행 시 앱 최소화"])
        self.assertEqual(self.window.ui.minimizeEnableBtn.text(), "실행 시 최소화")
        self.assertFalse(self.panel.minimize_app_on_task_start_enabled())
        self.panel.task_minimize_app_row.title_label.activated.emit()
        self.assertTrue(self.panel.minimize_app_on_task_start_enabled())
        self.panel.task_minimize_app_row.description_label.activated.emit()
        self.assertFalse(self.panel.minimize_app_on_task_start_enabled())

    def test_round_trip_keeps_target_and_startup_minimize_independent(self):
        self.enable_app_minimize()
        stored = SettingsStore(self.panel.store.config_path).load()
        self.assertTrue(stored["general"]["minimize_app_on_task_start"])
        self.assertFalse(stored["general"]["minimize_enabled"])
        self.assertFalse(stored["startup"]["minimize_app"])
        with patch("app.winUI.AppRuntime", return_value=self.window.runtime):
            restored = fixtures.MainWindow()
        self.addCleanup(restored.deleteLater)
        self.assertTrue(restored.settings_panel.minimize_app_on_task_start_enabled())
        self.assertFalse(restored.ui.minimizeEnableBtn.isChecked())
        self.assertFalse(restored.settings_panel.startup_checkboxes["minimize_app"].isChecked())
        restored.settings_panel.task_minimize_app_checkbox.setChecked(False)
        self.assertFalse(SettingsStore(self.panel.store.config_path).load()["general"]["minimize_app_on_task_start"])

    def test_old_or_invalid_values_default_off_and_reset_clears_the_option(self):
        for value in (None, "true", 1, [], False, True):
            with self.subTest(value=value):
                self.panel.store.save({"general": {"minimize_app_on_task_start": value}})
                config = self.panel.store.load()
                self.assertIs(config["general"]["minimize_app_on_task_start"], value is True)
        self.panel.store.save({"general": {"minimize_enabled": True}})
        old = self.panel.store.load()
        self.assertFalse(old["general"]["minimize_app_on_task_start"])
        self.assertTrue(old["general"]["minimize_enabled"])
        self.enable_app_minimize()
        reset_settings_files(self.window.runtime.user_dir, deepcopy(DEFAULT_MAA_CONFIG))
        self.assertFalse(self.panel.store.load()["general"]["minimize_app_on_task_start"])

    def test_start_button_minimizes_once_only_after_worker_is_started(self):
        self.enable_app_minimize()
        events = []
        worker = MagicMock()
        worker.start.side_effect = lambda: events.append("start")
        with patch.object(self.window, "showMinimized", side_effect=lambda: events.append("minimize")):
            self.start_mock_task(worker)
        self.assertEqual(events, ["start", "minimize"])
        self.assertTrue(self.window.isRunning)

    def test_default_off_keeps_app_visible_even_when_target_minimize_is_on(self):
        self.window.ui.minimizeEnableBtn.setChecked(True)
        with patch.object(self.window, "showMinimized") as minimize:
            _worker, factory = self.start_mock_task()
        minimize.assert_not_called()
        self.assertTrue(factory.call_args.args[2])

    def test_app_minimize_does_not_turn_on_target_minimization(self):
        self.enable_app_minimize()
        with patch.object(self.window, "showMinimized") as minimize:
            _worker, factory = self.start_mock_task()
        minimize.assert_called_once_with()
        self.assertFalse(factory.call_args.args[2])
        self.assertFalse(self.panel.startup_checkboxes["minimize_app"].isChecked())

    def test_user_restore_is_preserved_during_repeat_clicks_stop_and_completion(self):
        window = self.window
        self.enable_app_minimize()
        window.show()
        QApplication.processEvents()
        with patch.object(window, "showMinimized", wraps=window.showMinimized) as minimize:
            self.start_mock_task()
            self.assertTrue(window.isMinimized())
            window.showNormal()
            QApplication.processEvents()
            self.assertFalse(window.isMinimized())
            window.on_task_start()  # A duplicate start while running is ignored.
            stop_worker = MagicMock(succeeded=True, result_message="stopped")
            with patch("app.winUI.StopWorker", return_value=stop_worker):
                window.ui.workStartBtn.click()
            window.on_stop_worker_finished()
            window.on_task_finished()
            QApplication.processEvents()
            minimize.assert_called_once_with()
            self.assertFalse(window.isMinimized())
            self.assertFalse(window.isRunning)

    def test_completion_does_not_restore_a_still_minimized_app(self):
        self.enable_app_minimize()
        self.window.show()
        self.start_mock_task()
        self.assertTrue(self.window.isMinimized())
        self.window.on_task_finished()
        QApplication.processEvents()
        self.assertTrue(self.window.isMinimized())

    def test_start_guards_never_minimize_or_spawn(self):
        self.enable_app_minimize()
        guards = ((self.window, "_closing", True), (self.window, "worker", MagicMock()),
                  (self.window.runtime, "interface_missing", True),
                  (self.window.app_startup, "active", True),
                  (self.window.daily_restart, "active", True))
        with patch.object(self.window, "showMinimized") as minimize, patch("app.winUI.RuntimeWorker") as factory:
            for owner, name, value in guards:
                with self.subTest(guard=name), patch.object(owner, name, value):
                    self.window.on_task_start()
            minimize.assert_not_called()
            factory.assert_not_called()

    def test_preflight_wait_defers_minimize_until_the_start_is_accepted(self):
        self.enable_app_minimize()
        callbacks = []
        with patch.object(self.window.monitor, "prepare_task_start", side_effect=lambda cb: callbacks.append(cb) or True), \
                patch.object(self.window, "showMinimized") as minimize, \
                patch("app.winUI.RuntimeWorker", return_value=MagicMock()) as factory:
            self.window.on_task_start()
            minimize.assert_not_called()
            factory.assert_not_called()
            with patch.object(self.window.monitor, "prepare_task_start", return_value=False):
                callbacks[0]()
            minimize.assert_called_once_with()
            factory.assert_called_once()

    def test_worker_start_failure_does_not_minimize(self):
        self.enable_app_minimize()
        worker = MagicMock()
        worker.start.side_effect = RuntimeError("cannot start")
        with patch.object(self.window, "showMinimized") as minimize, \
                patch("app.winUI.RuntimeWorker", return_value=worker):
            with self.assertRaisesRegex(RuntimeError, "cannot start"):
                self.window.on_task_start()
        minimize.assert_not_called()

    def test_changing_either_app_setting_does_not_minimize_immediately(self):
        with patch.object(self.window, "showMinimized") as minimize:
            self.panel.startup_checkboxes["minimize_app"].setChecked(True)
            self.enable_app_minimize()
            self.panel.task_minimize_app_checkbox.setChecked(False)
        minimize.assert_not_called()

    def test_minimum_width_general_rows_fit_and_align_in_both_themes(self):
        window, panel = self.window, self.panel
        window.resize(window.minimumWidth(), 800)
        window.show()
        window.ui.mainPages.setCurrentWidget(window.ui.settingTab)
        for theme in ("light", "dark"):
            panel.theme_combo.setCurrentIndex(panel.theme_combo.findData(theme))
            panel.navigation.setCurrentRow(panel.section_order().index("general"))
            QApplication.processEvents()
            row = panel.task_minimize_app_row
            previous = panel.minimize_checkbox.parentWidget()
            self.assertEqual(row.height(), previous.height())
            self.assertEqual(panel.task_minimize_app_checkbox.mapTo(panel, QPoint()).x(),
                             panel.minimize_checkbox.mapTo(panel, QPoint()).x())
            self.assertLess(row.title_label.geometry().right(), panel.task_minimize_app_checkbox.x())
            self.assertEqual(panel.detail_scroll.horizontalScrollBar().maximum(), 0)
            self.assertEqual(window.width(), window.minimumWidth())
            directory = os.environ.get("MAABA_UI_PREVIEW_DIR")
            if directory:
                Path(directory).mkdir(parents=True, exist_ok=True)
                panel.grab().save(str(Path(directory) / f"task_app_minimize_{theme}.png"))

    def test_multi_run_minimizes_once_per_batch_not_per_instance(self):
        self.enable_app_minimize()
        ids = self.prepare_instances()
        with patch.object(self.window.monitor, "prepare_task_start", return_value=False), \
                patch.object(self.window, "showMinimized") as minimize:
            self.window.multi_panel.start_sessions(ids)
        minimize.assert_called_once_with()
        self.assertTrue(all(self.window.multi_panel.manager.sessions[sid].active for sid in ids))

    def test_multi_connection_check_does_not_minimize_the_app(self):
        self.enable_app_minimize()
        ids = self.prepare_instances()
        with patch.object(self.window.monitor, "prepare_task_start", return_value=False), \
                patch.object(self.window, "showMinimized") as minimize:
            self.window.multi_panel.start_sessions(ids, "check")
        minimize.assert_not_called()

    def test_multi_rejected_or_failed_start_does_not_minimize(self):
        self.enable_app_minimize()
        ids = self.prepare_instances()
        manager = self.window.multi_panel.manager
        for result, error in ((False, None), (None, RuntimeError("cannot start"))):
            with self.subTest(error=error), \
                    patch.object(self.window.monitor, "prepare_task_start", return_value=False), \
                    patch.object(self.window, "showMinimized") as minimize, \
                    patch.object(manager, "start", return_value=result, side_effect=error):
                self.window.multi_panel.start_sessions(ids)
                minimize.assert_not_called()
