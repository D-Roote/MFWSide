"""Wall-clock scheduling with serial, cancelable target restarts after SDK cleanup."""
from copy import deepcopy
from datetime import datetime
from time import monotonic

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from app.dailyRestart import next_daily_restart, restart_target
from app.runtime import PROGRAM_LAUNCH_ENTRY
from app.sessionRuntime import serialize_target
from app.startupSettings import normalize_startup_config
from app.startupUI import AppStartupDialog


class DailyRestartWorker(QThread):
    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.jobs = deepcopy(jobs)
        self.results = []

    def run(self):
        for job in self.jobs:
            if self.isInterruptionRequested():
                break
            try:
                connection = restart_target(job["snapshot"], job["connection"], self.isInterruptionRequested)
                self.results.append({**job, "connection": connection, "error": ""})
            except Exception as error:
                self.results.append({**job, "error": str(error)})


class DailyRestartCoordinator(QObject):
    activity_changed = Signal(bool)
    shutdown_ready = Signal()

    def __init__(self, window, clock=None):
        super().__init__(window)
        self.window, self.clock = window, clock or datetime.now
        self.active = self.closing = self.cancelled = False
        self.stopping_tasks = False
        self.worker = self.dialog = None
        self.next_run = None
        self.configuration = None
        self.jobs = []
        self.phase = ""
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        window.settings_panel.startup_changed.connect(self.configure)
        self.configure()

    @property
    def ready_to_close(self):
        return self.worker is None

    @Slot()
    def configure(self):
        options = normalize_startup_config(self.window.settings_panel.startup_settings())
        enabled = (options["daily_restart"] and bool(self.window.settings_panel.program_settings().get("resolved_path"))
                   and getattr(self.window.runtime, "interface_missing", False) is not True)
        configuration = (enabled, options["daily_restart_time"])
        if configuration == self.configuration:
            return
        self.configuration = configuration
        self.next_run = next_daily_restart(self.clock(), configuration[1]) if enabled else None
        if self.active:
            self.cancel()
        if not self.closing and (enabled or self.active):
            self.timer.start()
        else:
            self.timer.stop()

    def tick(self):
        if self.closing:
            return
        if self.active:
            self._advance()
            return
        now = self.clock()
        if self.next_run is not None and now >= self.next_run:
            # Advance first: duplicate timer events or failures never retry this day.
            self.next_run = next_daily_restart(now, self.configuration[1])
            self._begin()

    def _set_active(self, active):
        self.active = active
        if not active and self.dialog is not None:
            dialog, self.dialog = self.dialog, None
            dialog.hide()
            dialog.deleteLater()
        self.activity_changed.emit(active)

    def _begin(self):
        window = self.window
        if window._closing or window._completion_pending or window._reset_settings_pending:
            return
        if not window.settings_panel.program_settings().get("resolved_path"):
            window.append_log("실행 경로가 없어 일일 초기화 재시작을 생략했습니다.")
            return
        try:
            ids = [session.id for session in window.multi_panel.manager.sessions.values()
                   if session.active and session.mode == "run"]
            jobs = []
            for session_id in ids or [None]:
                session = window.multi_panel.manager.sessions.get(session_id)
                snapshot = (window.multi_panel.snapshot_for_session(session_id) if session is not None
                            else window.multi_execution_snapshot())
                snapshot["queue"] = [(entry, override) for entry, override in snapshot["queue"]
                                     if entry != PROGRAM_LAUNCH_ENTRY]
                if not snapshot["queue"]:
                    raise ValueError("일일 재시작 후 실행할 작업이 선택되지 않았습니다.")
                connection = deepcopy(session.connection) if session is not None else self._base_connection(snapshot)
                jobs.append({"id": session_id, "snapshot": snapshot, "connection": connection})
        except (KeyError, TypeError, ValueError, RuntimeError) as error:
            window.append_log(f"일일 초기화 재시작을 생략했습니다: {error}")
            return
        self.jobs, self.cancelled, self.phase = jobs, False, "stopping"
        self.stop_deadline = monotonic() + 60
        self._set_active(True)
        self.dialog = AppStartupDialog(window)
        self.dialog.setWindowTitle("일일 초기화 재시작")
        self.dialog.title_label.setText("일일 초기화 재시작")
        self.dialog.description.setText("진행 중인 작업을 정리한 뒤 대상 프로그램과 작업을 다시 시작합니다.")
        self.dialog.countdown.set_pending("준비", "재시작")
        self.dialog.start_button.hide()
        self.dialog.cancel_button.setText("재시작 취소")
        self.dialog.cancel_requested.connect(self.cancel)
        self.dialog.show()
        window.append_log("일일 초기화 시간이 되어 대상 프로그램과 작업을 다시 시작합니다.")
        if window.app_startup.active:
            window.app_startup.cancel()
        self.stopping_tasks = True
        try:
            if window.worker is not None:
                window.on_task_stop()
            window.multi_panel.manager.stop_all()
        finally:
            self.stopping_tasks = False
        self._advance()

    def _base_connection(self, snapshot):
        name = snapshot["controller"].get("name")
        monitor = self.window.monitor
        target = monitor.service.connected_target
        if target is None:
            target = next((item for item in monitor.targets if item.key == monitor.target_key), None)
        if target is not None and monitor.preset_name == name:
            return {"preset": name, "target": serialize_target(target), "preferences": deepcopy(monitor.preferences)}
        hwnd = getattr(self.window.runtime, "_target_hwnd", None)
        if type(hwnd) is int and hwnd > 0:
            from app.afterActions import capture_window_target
            captured = capture_window_target(hwnd)
            return {"preset": name, "target": {"kind": "Win32", "hwnd": hwnd, "pid": captured.pid,
                                                "process_path": captured.path}}
        return {"preset": name}

    def _advance(self):
        if not self.active or self.cancelled or self.phase not in ("stopping", "monitor"):
            return
        window = self.window
        if monotonic() >= self.stop_deadline:
            window.append_log("작업 정리 제한 시간을 초과하여 일일 재시작을 중단했습니다.")
            self.cancel()
            return
        if self.phase == "monitor":
            return
        if (window.worker is not None or window.stop_worker is not None or window.isRunning
                or not window.multi_panel.ready_to_close or not window.app_startup.ready_to_close
                or window.settings_panel.program_test_busy or window.settings_panel.management.busy
                or window._adb_app_stop_worker is not None or window._completion_pending):
            return
        if window.monitor.prepare_task_start(self._start_worker):
            self.phase = "monitor"
            return
        self._start_worker()

    def _start_worker(self):
        if not self.active or self.cancelled or self.closing or self.worker is not None:
            return
        self.phase = "restarting"
        self.worker = worker = DailyRestartWorker(self.jobs, self)
        worker.finished.connect(self._finished)
        try:
            worker.start()
        except RuntimeError as error:
            self.worker = None
            worker.deleteLater()
            self.window.append_log(f"일일 재시작 실행 실패: {error}")
            self._set_active(False)

    @Slot()
    def _finished(self):
        worker, self.worker = self.worker, None
        if worker is None:
            return
        results = worker.results
        worker.deleteLater()
        self._set_active(False)
        if self.cancelled or self.closing or self.window._closing:
            if self.closing:
                self.shutdown_ready.emit()
            return
        for job in results:
            if job["error"]:
                self.window.append_log(f"일일 재시작 실패: {job['error']}")
                continue
            if job["id"] is None:
                self.window._startup_queue_override = deepcopy(job["snapshot"]["queue"])
                self.window.on_task_start()
            elif job["id"] in self.window.multi_panel.manager.sessions:
                self.window.multi_panel.manager.configure(job["id"], job["connection"])
                self.window.multi_panel.start_sessions([job["id"]], snapshot=job["snapshot"])

    def cancel(self):
        if not self.active or self.cancelled:
            return
        self.cancelled = True
        self.window.append_log("일일 초기화 재시작이 취소되었습니다.")
        if self.worker is not None:
            self.worker.requestInterruption()
            if self.dialog is not None:
                self.dialog.set_cancelling()
        else:
            if self.window.monitor.pending_start == self._start_worker:
                self.window.monitor.pending_start = None
            self._set_active(False)

    def shutdown(self):
        self.closing = True
        self.timer.stop()
        self.cancel()
        return self.ready_to_close
