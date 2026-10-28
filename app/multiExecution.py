"""Process-owned execution sessions and JSON-only GUI/worker boundaries."""
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
import multiprocessing as mp
import os
from pathlib import Path
from queue import Empty, Full
import threading
import time
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, Signal

from app.monitoring import MonitoringService, supported_presets
from app.sessionRuntime import SessionRuntime, needs_desktop_lock, resolve_preset, serialize_target, target_identity
from app.adbWindow import window_identity
from app.sessionIPC import DesktopLease, SessionEventInbox, SharedFlag


FAILED_SESSION_CLEANUP_SECONDS = 8.0
EXIT_EVENT_DRAIN_SECONDS = 1.0


ACTIVE_STATES = {"starting", "waiting", "running", "checking", "stopping"}
STATE_LABELS = {"idle": "연결 대상 선택", "ready": "연결 확인 완료 · 실행 시 재연결", "starting": "시작 준비",
                "waiting": "Win32 입력 대기", "running": "작업 실행 중", "checking": "연결 확인 중",
                "stopping": "중지 및 정리 중", "completed": "작업 완료", "stopped": "작업 중지", "failed": "실패"}


def discover_connections(snapshot, preferences=None, preset_name=None):
    presets = supported_presets(snapshot["interface"], snapshot["resource"])
    preferred = snapshot.get("controller", {}).get("name")
    presets.sort(key=lambda preset: preset["name"] != preferred)
    seen, found, errors = set(), [], []
    for preset in presets:
        if preset_name is not None and preset["name"] != preset_name:
            continue
        service = MonitoringService(snapshot["interface"], snapshot["resource"],
                                    Path(snapshot["user_dir"]) / "sessions" / "discovery")
        try:
            for target in service.discover(preset["name"], preferences, snapshot.get("program")):
                if target.kind == "Win32" and target.pid == os.getpid():
                    continue
                connection = {"preset": preset["name"], "target": serialize_target(target),
                              "preferences": deepcopy(preferences or {}), "emulator": {}}
                if target.kind == "Adb":
                    connection["preferences"]["address"] = target.key
                identity = target_identity(connection)
                if identity and identity not in seen:
                    seen.add(identity)
                    found.append(connection)
        except Exception as error:
            errors.append(f"{preset.get('label') or preset['name']}: {error}")
        finally:
            service.close()
    return found, errors


def session_process(snapshot, connection, mode, stop_event, events, desktop_lock):
    """Top-level spawn target: never import or create GUI widgets here."""
    from app.crashDiagnostics import enable_fault_log
    from app.afterActions import capture_window_target
    from maa.toolkit import Toolkit
    runtime = None
    locked = False
    done = threading.Event()
    stopper = None
    outcome = {"succeeded": False, "message": "세션을 시작하지 못했습니다.", "target": None}

    def send(kind, value):
        try:
            events.put((kind, value), timeout=2 if kind in ("result", "disconnected") else 0)
        except (Full, EOFError, OSError, ValueError):
            pass  # A stalled GUI cannot accumulate an unbounded native log queue.

    try:
        preset = resolve_preset(snapshot, connection)
        if needs_desktop_lock(preset, snapshot.get("minimize", False)):
            send("state", "waiting")
            while not desktop_lock.acquire(timeout=0.1):
                if stop_event.is_set():
                    outcome["message"] = "작업이 중지되었습니다."
                    return
            locked = True
        if stop_event.is_set():
            outcome["message"] = "작업이 중지되었습니다."
            return
        directory = Path(snapshot["user_dir"]) / "sessions" / snapshot["session_id"] / uuid4().hex
        enable_fault_log(directory)
        runtime = SessionRuntime(snapshot, connection, directory)
        runtime.log_sink.set_log_callback(lambda message: send("log", message))
        if not Toolkit.init_option(str(directory), snapshot.get("settings", {})):
            raise RuntimeError("세션 연결 도구 초기화에 실패했습니다.")
        runtime._toolkit_initialized = True

        def cancel():
            while not done.wait(0.05):
                if stop_event.is_set() and runtime.tasker is not None:
                    runtime.stop_task()
                    return
        stopper = threading.Thread(target=cancel, daemon=True)
        stopper.start()
        if mode == "check":
            send("state", "checking")
            created, message = runtime._create_controller()
            if not created:
                raise RuntimeError(message)
            outcome["succeeded"], outcome["message"] = runtime._execute_controller()
        else:
            send("state", "starting")
            initialized, message = runtime.initialize(
                snapshot.get("controller"), program_settings=snapshot.get("program"),
                execution_queue=snapshot["queue"], minimize_window=snapshot["minimize"],
                cancellation_requested=stop_event.is_set)
            if not initialized:
                outcome["message"] = message
                return
            if preset["type"] == "Win32" and snapshot.get("after_actions", {}).get("close_program"):
                from dataclasses import asdict
                outcome["target"] = asdict(capture_window_target(runtime._target_hwnd))
            if preset["type"] == "Adb" and snapshot.get("after_actions", {}).get("close_app"):
                from dataclasses import asdict
                from app.adbAppStop import capture_adb_app_stop_target
                try:
                    outcome["adb_app_stop_target"] = asdict(capture_adb_app_stop_target(
                        runtime._adb_execution_connection, stop_event.is_set))
                except (OSError, RuntimeError, ValueError) as error:
                    send("log", f"완료 후 앱 종료 대상 확인 실패: {error}")
            send("state", "running")
            runtime.start_connection_watch(lambda message: send("disconnected", message))
            outcome["succeeded"], outcome["message"] = runtime.run_task(
                snapshot["queue"], minimize_window=snapshot["minimize"], cancellation_requested=stop_event.is_set)
    except Exception as error:
        outcome["message"] = str(error)
    finally:
        done.set()
        if stopper is not None:
            stopper.join(6)
        if runtime is not None:
            runtime.stop_connection_watch()
            connection = getattr(runtime, "connection", {})
            if isinstance(connection, dict) and "emulator" in connection:
                outcome["emulator"] = deepcopy(connection["emulator"])
            try:
                success, message = runtime.release_session()
                if not success:
                    outcome["succeeded"] = False
                    outcome["message"] += "\n" + message
            except Exception as error:
                outcome["succeeded"] = False
                outcome["message"] += "\n정리 실패: " + str(error)
        if stop_event.is_set():
            outcome["succeeded"] = False
            outcome["message"] += "\n작업이 중지되었습니다."
        outcome["message"] = "\n".join(dict.fromkeys(outcome["message"].splitlines()))
        send("result", outcome)
        # DesktopLease.release is a no-op: the parent admits another session
        # only after this process exits, including an abnormal native exit.
        if locked:
            desktop_lock.release()


@dataclass
class Session:
    id: str
    title: str
    connection: dict = field(default_factory=dict)
    state: str = "idle"
    process: object = None
    events: object = None
    stop_event: object = None
    mode: str = "run"
    result: dict | None = None
    remove_pending: bool = False
    logs: deque = field(default_factory=lambda: deque(maxlen=200))
    task_preset: str = ""
    desktop_lease: object = None
    needs_desktop: bool = False
    failure_reason: str = ""
    failure_deadline: float = 0.0
    exited_at: float | None = None
    termination_requested: bool = False
    result_received_at: float | None = None

    @property
    def active(self):
        return self.process is not None


class SessionManager(QObject):
    changed = Signal(str)
    log = Signal(str, str)
    activity_changed = Signal()
    wave_finished = Signal(object, object)

    def __init__(self, parent=None, context=None, event_channel_factory=None):
        super().__init__(parent)
        self.context = context or mp.get_context("spawn")
        self.event_channel_factory = event_channel_factory or SessionEventInbox
        self.desktop_owner = None
        self.sessions = {}
        self.wave = None
        self.closing = False
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.poll)

    @property
    def active(self):
        return any(session.active for session in self.sessions.values())

    def add(self, connection=None, title=None, task_preset=""):
        connection = deepcopy(connection or {})
        identity = target_identity(connection)
        if identity and any(target_identity(session.connection) == identity for session in self.sessions.values()):
            return None
        number = 1
        while f"인스턴스 {number}" in {s.title for s in self.sessions.values()}:
            number += 1
        task_preset = task_preset if isinstance(task_preset, str) and len(task_preset) <= 256 else ""
        session = Session(uuid4().hex, title or f"인스턴스 {number}", connection, task_preset=task_preset)
        self.sessions[session.id] = session
        self.changed.emit(session.id)
        return session.id

    def set_task_preset(self, session_id, preset_id):
        session = self.sessions[session_id]
        if session.active:
            raise ValueError("실행/연결 확인 중에는 작업 프리셋을 변경할 수 없습니다.")
        if not isinstance(preset_id, str) or len(preset_id) > 256:
            raise ValueError("올바른 작업 프리셋을 선택하세요.")
        session.task_preset = preset_id
        self.changed.emit(session_id)

    def configure(self, session_id, connection):
        session = self.sessions[session_id]
        if session.active:
            raise ValueError("실행/연결 확인 중에는 연결 대상을 변경할 수 없습니다.")
        identity = target_identity(connection)
        if identity and any(other.id != session_id and target_identity(other.connection) == identity
                            for other in self.sessions.values()):
            raise ValueError("이미 다른 인스턴스에 등록된 연결 대상입니다.")
        session.connection = deepcopy(connection)
        session.state = "idle"
        session.result = None
        self.changed.emit(session_id)

    def validate_start(self, session_id, snapshot):
        session = self.sessions[session_id]
        preset = resolve_preset(snapshot, session.connection)
        # ADB convenience minimization must never manipulate another session's
        # Win32 window, even if that session is only waiting for the input lock.
        target = session.connection.get("emulator") if preset["type"] == "Adb" else session.connection["target"]
        if not target or (preset["type"] == "Adb" and not snapshot.get("minimize")):
            return
        identity = (target.get("pid"), target.get("hwnd"))
        for other in self.sessions.values():
            if other.id == session_id:
                continue
            candidate = other.connection.get("target") or {}
            emulator = other.connection.get("emulator") or {}
            candidates = [candidate] if candidate.get("kind") == "Win32" else [emulator]
            if any(identity == (item.get("pid"), item.get("hwnd")) for item in candidates):
                raise ValueError("편의 최소화 창이 다른 인스턴스의 창과 겹칩니다. 창 선택을 해제하세요.")

    def mark_wave_failure(self, session_id, message):
        if self.wave is not None and not any(result["id"] == session_id and not result["succeeded"]
                                             for result in self.wave["results"]):
            self.wave["results"].append({"id": session_id, "succeeded": False, "message": str(message)})

    def start(self, session_id, snapshot, mode="run"):
        session = self.sessions[session_id]
        if self.closing or session.active:
            return False
        self.validate_start(session_id, snapshot)
        request = deepcopy(snapshot)
        request["session_id"] = session_id
        request["client_pid"] = os.getpid()
        if mode == "run" and not request.get("queue"):
            raise ValueError("실행할 작업을 선택하거나 작업 프리셋을 확인하세요.")
        session.mode, session.result = mode, None
        session.failure_reason = ""
        session.failure_deadline = 0.0
        session.exited_at = None
        session.termination_requested = False
        session.result_received_at = None
        session.needs_desktop = needs_desktop_lock(resolve_preset(request, session.connection), request.get("minimize", False))
        session.desktop_lease = DesktopLease(SharedFlag(self.context))
        session.logs.clear() if request.get("clear_log") else None
        session.stop_event = SharedFlag(self.context)
        session.events = self.event_channel_factory(self.context)
        session.process = self.context.Process(
            target=session_process, args=(request, deepcopy(session.connection), mode, session.stop_event,
                                         session.events.sender, session.desktop_lease), daemon=False)
        session.state = "checking" if mode == "check" else "starting"
        try:
            session.process.start()
            session.events.close_sender()
            session.events.start()
        except Exception as error:
            if session.process.is_alive():
                self._fail_session(session, f"세션 통신을 시작하지 못했습니다: {error}")
                self.timer.start()
            else:
                session.process.close()
                session.process = None
                session.state = "failed"
            session.events.close()
            self.mark_wave_failure(session_id, error)
            self.changed.emit(session_id)
            self.activity_changed.emit()
            raise
        self._grant_desktop_access()
        if mode == "run":
            if self.wave is None:
                self.wave = {"ids": set(), "results": [], "actions": deepcopy(request.get("after_actions", {}))}
            self.wave["ids"].add(session_id)
        self.timer.start()
        self.changed.emit(session_id)
        self.activity_changed.emit()
        return True

    def stop(self, session_id):
        session = self.sessions.get(session_id)
        if session is not None and session.active:
            session.stop_event.set()
            session.state = "stopping"
            self.changed.emit(session_id)

    def stop_all(self):
        for session_id in list(self.sessions):
            self.stop(session_id)

    def remove(self, session_id):
        session = self.sessions[session_id]
        if session.active:
            session.remove_pending = True
            self.stop(session_id)
        else:
            del self.sessions[session_id]
            self.changed.emit(session_id)

    def _grant_desktop_access(self):
        if self.desktop_owner is not None:
            return
        for session in self.sessions.values():
            if session.active and session.needs_desktop and not session.stop_event.is_set():
                self.desktop_owner = session.id
                session.desktop_lease.ready.set()
                return

    def _fail_session(self, session, message):
        if session.failure_reason:
            return
        session.failure_reason = str(message).strip() or "세션 연결 또는 통신 오류가 발생했습니다."
        session.failure_deadline = time.monotonic() + FAILED_SESSION_CLEANUP_SECONDS
        session.stop_event.set()
        session.state = "stopping"
        self.changed.emit(session.id)

    def poll(self):
        finished = False
        for session in list(self.sessions.values()):
            if not session.active:
                continue
            was_alive = session.process.is_alive()
            for _ in range(100 if was_alive else 600):
                try:
                    kind, value = session.events.get_nowait()
                except Empty:
                    break
                except (EOFError, OSError, ValueError, TypeError) as error:
                    self._fail_session(session, f"세션 통신을 읽지 못했습니다: {error}")
                    break
                if kind == "log":
                    session.logs.append(str(value))
                    self.log.emit(session.id, str(value))
                elif kind == "state" and isinstance(value, str) and value in ACTIVE_STATES and session.state != "stopping":
                    session.state = value
                    self.changed.emit(session.id)
                elif kind == "result":
                    if isinstance(value, dict) and isinstance(value.get("succeeded"), bool) and isinstance(value.get("message"), str):
                        session.result = value
                        if session.result_received_at is None:
                            session.result_received_at = time.monotonic()
                        if not value["succeeded"] and not session.stop_event.is_set():
                            self._fail_session(session, value["message"])
                    else:
                        self._fail_session(session, "세션 실행 결과의 형식이 올바르지 않습니다.")
                elif kind == "disconnected" and session.state != "stopping":
                    self._fail_session(session, value)
            if session.events.error:
                self._fail_session(session, session.events.error)
            if was_alive:
                now = time.monotonic()
                if (session.result_received_at is not None and not session.stop_event.is_set()
                        and now - session.result_received_at >= FAILED_SESSION_CLEANUP_SECONDS):
                    self._fail_session(session, "실행 결과 수신 후 세션 프로세스가 종료되지 않아 해당 인스턴스만 정리합니다.")
                    session.failure_deadline = now  # The terminal-result cleanup grace has already elapsed.
                if session.failure_reason and time.monotonic() >= session.failure_deadline and not session.termination_requested:
                    try:
                        session.process.terminate()  # Only this failed app-owned worker, never ADB/server/other sessions.
                        session.termination_requested = True
                    except (OSError, ValueError) as error:
                        session.failure_reason += f"\n세션 프로세스 종료 요청 실패: {error}"
                        session.failure_deadline = time.monotonic() + 1
                continue
            if session.exited_at is None:
                session.exited_at = time.monotonic()
            if (session.process.exitcode == 0 and session.result is None and not session.events.reader_done
                    and time.monotonic() - session.exited_at < EXIT_EVENT_DRAIN_SECONDS):
                continue  # Let its receiver finish; never block other sessions or the GUI.
            finished = True
            session.process.join(timeout=0)
            cancelled = session.stop_event.is_set() and not session.failure_reason
            result = session.result or {"succeeded": False, "message": "실행 프로세스가 결과 없이 종료되었습니다."}
            if session.failure_reason:
                result = {**result, "succeeded": False, "message": session.failure_reason}
            exit_code = session.process.exitcode
            if exit_code not in (None, 0):
                result = {**result, "succeeded": False,
                          "message": result["message"] + f"\n실행 프로세스 비정상 종료: {exit_code:#x}. 세션 진단 로그를 확인하세요."}
            session.result = result
            if isinstance(result.get("emulator"), dict) and (not result["emulator"] or window_identity(result["emulator"])):
                session.connection["emulator"] = deepcopy(result["emulator"])
            session.state = ("stopped" if cancelled else "ready" if result["succeeded"] and session.mode == "check"
                             else "completed" if result["succeeded"] else "failed")
            session.process.close()
            session.process = None
            session.events.close()
            session.events = None
            if self.desktop_owner == session.id:
                self.desktop_owner = None
            if session.mode == "run" and self.wave is not None:
                self.wave["results"].append({"id": session.id, "connection": deepcopy(session.connection), **result,
                                              "succeeded": bool(result["succeeded"] and not cancelled)})
                self.wave["ids"].discard(session.id)
            self.log.emit(session.id, result["message"])
            if session.remove_pending:
                del self.sessions[session.id]
            self.changed.emit(session.id)
        self._grant_desktop_access()
        if not self.active:
            self.timer.stop()
            if self.wave is not None:
                wave, self.wave = self.wave, None
                self.wave_finished.emit(wave["results"], wave["actions"])
        if finished:
            self.activity_changed.emit()

    def saved_sessions(self):
        return [{"title": session.title, "connection": deepcopy(session.connection), "task_preset": session.task_preset}
                for session in self.sessions.values()]

    def restore(self, records):
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and isinstance(record.get("connection"), dict):
                    connection = record["connection"]
                    if (connection.get("target") is not None and not isinstance(connection["target"], dict)
                            or not isinstance(connection.get("preferences", {}), dict)
                            or not isinstance(connection.get("emulator", {}), dict)):
                        continue
                    self.add(connection, str(record.get("title", "인스턴스"))[:128], record.get("task_preset", ""))

    def shutdown(self):
        self.closing = True
        self.stop_all()
