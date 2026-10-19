"""One-shot startup orchestration after the window and user settings are ready."""
from copy import deepcopy

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from app.programTest import request_program_launch
from app.runtime import PROGRAM_LAUNCH_ENTRY, PROGRAM_POST_WINDOW_WAIT_SECONDS
from app.startupSettings import normalize_startup_config
from app.startupUI import AppStartupDialog


class StartupLaunchWorker(QThread):
    def __init__(self, program, minimize, parent=None):
        super().__init__(parent)
        self.program, self.minimize = deepcopy(program), minimize
        self.result = (False, "시작 실행을 완료하지 못했습니다.")
        self.identity = None

    def run(self):
        try:
            self.identity = request_program_launch(self.program, self.isInterruptionRequested, minimize=self.minimize)
            self.result = (True, "대상 프로그램 실행을 요청했습니다.")
        except Exception as error:
            self.result = (False, f"시작 실행 실패: {error}")


class AppStartupCoordinator(QObject):
    activity_changed = Signal(bool)
    shutdown_ready = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.started = False
        self.active = False
        self.closing = False
        self.cancelled = False
        self.completed = False
        self.worker = None
        self.queue = []
        self.launch_identity = None
        self.auto_tasks = False
        self.task_wait_seconds = 0
        self._wait_phase = None
        self._task_wait_started = False
        self.dialog = None
        self._minimize_after_dialog = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._finish_wait)
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(200)
        self.progress_timer.timeout.connect(self._update_dialog)

    @property
    def ready_to_close(self):
        return self.worker is None

    def _status(self, message):
        self.window.append_log(message)

    def _set_active(self, value):
        self.active = value
        if not value:
            self.progress_timer.stop()
            if self.dialog is not None:
                dialog, self.dialog = self.dialog, None
                dialog.hide()
                dialog.deleteLater()
            if self._minimize_after_dialog and not self.closing and not self.window._closing:
                self.window.showMinimized()
            self._minimize_after_dialog = False
        self.activity_changed.emit(value)

    def _show_dialog(self):
        if self.dialog is None:
            self.dialog = AppStartupDialog(self.window)
            self.dialog.cancel_requested.connect(self.cancel)
            self.dialog.start_requested.connect(self.start_now)
        return self.dialog

    def _start_wait(self, phase, seconds):
        self._wait_phase = phase
        self.timer.start(seconds * 1000)
        self._show_dialog()
        self._update_dialog()
        self.dialog.show()
        self.progress_timer.start()

    def _update_dialog(self):
        if self.dialog is not None and self._wait_phase is not None and not self.cancelled:
            milliseconds = max(0, self.timer.remainingTime())
            self.dialog.set_remaining((milliseconds + 999) // 1000,
                                      total_seconds=self.timer.interval() // 1000,
                                      preparing=self._wait_phase == "program")

    def start_now(self):
        # Never bypass the player loading phase or race an in-flight launch.
        if (self.active and not self.cancelled and not self.closing and self.worker is None
                and self._wait_phase == "tasks"):
            self._complete()

    def start(self):
        if self.started or self.closing or self.window._closing:
            return
        self.started = True
        panel = self.window.settings_panel
        options = normalize_startup_config(panel.startup_settings())
        if not options["launch_program"] and not options["auto_start_tasks"]:
            if options["minimize_app"]:
                self.window.showMinimized()
            return
        if getattr(self.window.runtime, "interface_missing", False) is True:
            self._status("interface.json이 없어 시작 자동 실행을 생략했습니다.")
            return
        if not self.window._can_test_program() or panel.program_test_busy or panel.management.busy:
            self._status("다른 작업이 진행 중이어서 시작 자동 실행을 생략했습니다.")
            return
        try:
            self.queue = deepcopy(self.window.build_execution_queue())
            program = panel.program_settings()
            if not program.get("resolved_path"):
                self._status("실행 경로가 없어 시작 자동 실행을 생략했습니다.")
                return
        except (KeyError, ValueError, TypeError) as error:
            self._status(f"시작 설정을 확인하세요: {error}")
            return
        self.auto_tasks = options["auto_start_tasks"]
        self.task_wait_seconds = options["task_wait_seconds"]
        # Do not hide an application-modal countdown behind a minimized owner.
        self._minimize_after_dialog = options["minimize_app"]
        self._set_active(True)
        if not options["launch_program"]:
            self._schedule_tasks()
            return
        # Startup launch is independent of the launch-task checkbox. Do not
        # launch the player a second time when starting the selected pipelines.
        self.queue = [entry for entry in self.queue if entry[0] != PROGRAM_LAUNCH_ENTRY]
        self.wait_seconds = (options["emulator_wait_seconds"] if program.get("manual_only")
                             else PROGRAM_POST_WINDOW_WAIT_SECONDS)
        self.worker = worker = StartupLaunchWorker(program, panel.minimize_enabled(), self)
        worker.finished.connect(self._launch_finished)
        self._status("시작 설정에 따라 대상 프로그램을 실행합니다.")
        dialog = self._show_dialog()
        dialog.set_launching()
        dialog.show()
        try:
            worker.start()
        except RuntimeError as error:
            self.worker = None
            worker.deleteLater()
            self._status(f"시작 실행 실패: {error}")
            self._set_active(False)

    @Slot()
    def _launch_finished(self):
        worker, self.worker = self.worker, None
        if worker is None:
            return
        succeeded, message = worker.result
        self.launch_identity = worker.identity if isinstance(worker.identity, dict) else None
        worker.deleteLater()
        if self.cancelled or self.closing:
            self._set_active(False)
            if self.closing:
                self.shutdown_ready.emit()
            return
        self._status(message)
        if not succeeded:
            self._set_active(False)
            return
        if self.auto_tasks and self.wait_seconds:
            self._status(f"다음 작업 시작까지 {self.wait_seconds}초 대기합니다.")
            self._start_wait("program", self.wait_seconds)
        else:
            self._schedule_tasks()

    def _finish_wait(self):
        phase, self._wait_phase = self._wait_phase, None
        if phase == "program":
            self._schedule_tasks()
        elif phase == "tasks":
            self._complete()

    def _schedule_tasks(self):
        if self.completed:
            return
        if self.cancelled or self.closing or self.window._closing:
            self._complete()
            return
        if self.auto_tasks and self.queue and self.task_wait_seconds:
            if self._task_wait_started:
                return
            self._task_wait_started = True
            self._status(f"작업 자동 시작까지 {self.task_wait_seconds}초 대기합니다.")
            self._start_wait("tasks", self.task_wait_seconds)
            return
        self._complete()

    def _complete(self):
        if self.completed:
            return
        self.timer.stop()
        self._wait_phase = None
        self.completed = True
        if self.cancelled or self.closing or self.window._closing:
            self._set_active(False)
            return
        self._set_active(False)
        if self.auto_tasks:
            if not self.queue:
                self._status("자동 시작할 작업이 선택되지 않았습니다.")
                return
            if not self.window.ui.workStartBtn.isEnabled():
                self._status("작업 설정 또는 실행 상태를 확인하세요. 자동 시작을 생략했습니다.")
                return
            self.window._startup_queue_override = deepcopy(self.queue)
            self.window.on_task_start()

    def cancel(self):
        if not self.active or self.cancelled:
            return
        self.cancelled = True
        self.timer.stop()
        self.progress_timer.stop()
        if self.worker is not None:
            if self.dialog is not None:
                self.dialog.set_cancelling()
            self.worker.requestInterruption()
        else:
            self._set_active(False)
        self._status("시작 자동 실행이 취소되었습니다. 이미 실행한 프로그램은 종료하지 않습니다.")

    def shutdown(self):
        self.closing = True
        self.cancel()
        return self.ready_to_close
