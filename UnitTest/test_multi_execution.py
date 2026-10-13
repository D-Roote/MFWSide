"""Session ownership, snapshots and process lifecycle without game input."""
from copy import deepcopy
from pathlib import Path
from queue import Queue
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from app.multiExecution import SessionManager, discover_connections, session_process
from app.monitoring import ConnectionTarget
from app.sessionRuntime import SessionRuntime, needs_desktop_lock, target_identity


def snapshot():
    presets = [{"name": "Win", "type": "Win32", "win32": {"window_regex": "Game", "mouse": "PostMessage"}},
               {"name": "Adb", "type": "Adb"}]
    return {"interface": {"controller": presets, "task": []}, "resource": {"path": ["."]},
            "interface_path": str(Path(__file__).parent / "fixtures/native_monitor/interface.json"),
            "user_dir": ".", "queue": [("Task", {"Node": {"enabled": False}})], "minimize": False,
            "controller": {"name": "Win"}, "program": {}, "settings": {}, "after_actions": {}}


def connection(kind="Win32", value=42):
    target = ({"kind": kind, "hwnd": value, "pid": 123, "key": str(value), "label": "Game", "process_path": "game.exe"}
              if kind == "Win32" else {"kind": "Adb", "key": str(value), "label": str(value)})
    return {"preset": "Win" if kind == "Win32" else "Adb", "target": target, "preferences": {}, "emulator": {}}


class FakeQueue(Queue):
    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, target, args, **kwargs):
        self.target, self.args = target, args
        self.alive = False
        self.closed = False
        self.exitcode = 0

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        assert not self.alive

    def close(self):
        self.closed = True


class FakeContext:
    Lock = staticmethod(threading.Lock)
    Event = staticmethod(threading.Event)
    Queue = staticmethod(lambda maxsize: FakeQueue(maxsize))
    Process = FakeProcess


class SessionManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.manager = SessionManager(context=FakeContext())
        self.addCleanup(self.manager.deleteLater)
        self.addCleanup(self.manager.timer.stop)

    def start(self, target=None, mode="run"):
        sid = self.manager.add(target or connection())
        self.manager.start(sid, snapshot(), mode)
        self.manager.timer.stop()
        return self.manager.sessions[sid]

    def finish(self, session, success=True):
        session.events.put(("result", {"succeeded": success, "message": "finished"}))
        session.process.alive = False
        self.manager.poll()

    def test_duplicate_target_even_with_different_preset_is_not_registered(self):
        self.assertIsNotNone(self.manager.add(connection()))
        duplicate = connection()
        duplicate["preset"] = "Other"
        self.assertIsNone(self.manager.add(duplicate))
        self.assertIsNotNone(self.manager.add(connection("Adb", "127.0.0.1:5555")))
        self.assertIsNone(self.manager.add(connection("Adb", "127.0.0.1:5555")))

    def test_reconfiguration_cannot_take_another_target_or_change_active_session(self):
        first = self.start()
        second = self.manager.add(connection(value=43))
        with self.assertRaises(ValueError):
            self.manager.configure(second, connection())
        with self.assertRaises(ValueError):
            self.manager.configure(first.id, connection(value=99))

    def test_snapshot_and_connection_are_deeply_copied(self):
        data, target = snapshot(), connection()
        sid = self.manager.add(target)
        self.manager.start(sid, data)
        target["target"]["hwnd"] = 999
        data["queue"][0][1]["Node"]["enabled"] = True
        args = self.manager.sessions[sid].process.args
        self.assertEqual(args[1]["target"]["hwnd"], 42)
        self.assertIs(args[0]["queue"][0][1]["Node"]["enabled"], False)

    def test_result_does_not_release_target_before_process_exits(self):
        session = self.start()
        session.events.put(("result", {"succeeded": True, "message": "finished"}))
        self.manager.poll()
        self.assertTrue(session.active)
        self.assertFalse(self.manager.start(session.id, snapshot()))
        process = session.process
        process.alive = False
        self.manager.poll()
        self.assertFalse(session.active)
        self.assertTrue(process.closed)
        self.assertEqual(session.state, "completed")

    def test_stop_and_delete_wait_for_process_exit_and_never_terminate(self):
        session = self.start()
        process = session.process
        self.manager.remove(session.id)
        self.assertTrue(session.stop_event.is_set())
        self.assertIn(session.id, self.manager.sessions)
        self.manager.poll()
        self.assertIn(session.id, self.manager.sessions)
        process.alive = False
        self.manager.poll()
        self.assertNotIn(session.id, self.manager.sessions)
        self.assertTrue(process.closed)

    def test_a_stop_does_not_stop_b_and_wave_waits_for_all(self):
        a = self.start()
        b = self.start(connection("Adb", "device-b"))
        waves = []
        self.manager.wave_finished.connect(lambda results, actions: waves.append(results))
        self.manager.stop(a.id)
        self.assertFalse(b.stop_event.is_set())
        self.finish(a)
        self.assertFalse(waves)
        self.finish(b)
        self.assertEqual(len(waves), 1)
        self.assertFalse(waves[0][0]["succeeded"])
        self.assertTrue(waves[0][1]["succeeded"])

    def test_connection_check_does_not_run_completion_actions(self):
        session = self.start(mode="check")
        finished = MagicMock()
        self.manager.wave_finished.connect(finished)
        self.finish(session)
        self.assertEqual(session.state, "ready")
        finished.assert_not_called()

    def test_exited_process_drains_log_backlog_before_result(self):
        session = self.start()
        for i in range(400):
            session.events.put(("log", str(i)))
        self.finish(session)
        self.assertEqual(session.state, "completed")
        self.assertEqual(len(session.logs), 200)

    def test_exit_without_result_is_failure(self):
        session = self.start()
        session.process.alive = False
        self.manager.poll()
        self.assertEqual(session.state, "failed")

    def test_native_crash_after_success_result_is_failure(self):
        session = self.start()
        session.process.exitcode = 0xc0000005
        self.finish(session)
        self.assertEqual(session.state, "failed")
        self.assertFalse(session.result["succeeded"])
        self.assertIn("0xc0000005", session.result["message"])

    def test_saved_connections_restore_idle_without_spawn(self):
        self.manager.add(connection())
        records = self.manager.saved_sessions()
        restored = SessionManager(context=FakeContext())
        self.addCleanup(restored.deleteLater)
        restored.restore(records)
        self.assertEqual(len(restored.sessions), 1)
        self.assertFalse(restored.active)

    def test_failed_second_process_poison_wave_and_keep_first_session(self):
        first = self.start()
        second = self.manager.add(connection(value=43))
        with patch.object(FakeProcess, "start", side_effect=OSError("spawn failed")):
            with self.assertRaises(OSError):
                self.manager.start(second, snapshot())
        waves = []
        self.manager.wave_finished.connect(lambda results, actions: waves.append(results))
        self.finish(first)
        self.assertTrue(any(not result["succeeded"] for result in waves[0]))
        self.assertFalse(self.manager.sessions[second].active)

    def test_adb_minimize_cannot_use_another_registered_win32_target(self):
        self.manager.add(connection())
        adb = connection("Adb", "device")
        adb["emulator"] = {"pid": 123, "hwnd": 42}
        sid = self.manager.add(adb)
        data = snapshot()
        data["minimize"] = True
        with self.assertRaises(ValueError):
            self.manager.start(sid, data)
        data["minimize"] = False
        self.assertTrue(self.manager.start(sid, data))

    def test_malformed_saved_nested_settings_are_skipped(self):
        self.manager.restore([{"connection": {"target": 12}}, {"connection": {"preferences": []}},
                              {"connection": {"emulator": "bad"}}, {"connection": connection()}])
        self.assertEqual(len(self.manager.sessions), 1)


class SessionRuntimeTests(unittest.TestCase):
    def runtime(self, target=None):
        with patch("app.runtime.create_user32", return_value=MagicMock()):
            return SessionRuntime(snapshot(), target or connection(), ".")

    def test_win32_requires_same_pid_and_path_not_first_matching_window(self):
        runtime = self.runtime()
        with patch("app.sessionRuntime.window_process_info", return_value=(123, "game.exe")):
            window, _ = runtime._find_target_window()
            self.assertEqual(window.hwnd, 42)
        with patch("app.sessionRuntime.window_process_info", return_value=(124, "game.exe")):
            self.assertIsNone(runtime._find_target_window()[0])

    def test_adb_never_calls_win32_resize_or_restore(self):
        runtime = self.runtime(connection("Adb", "device"))
        self.assertTrue(runtime._resize_window_for_task())
        self.assertTrue(runtime._minimize_window_for_task())
        self.assertTrue(runtime.release_session()[0])
        runtime._user32.MoveWindow.assert_not_called()
        runtime._user32.ShowWindow.assert_not_called()
        runtime._user32.SetWindowPlacement.assert_not_called()

    def test_adb_optional_minimize_only_targets_explicit_pid_and_does_not_restore(self):
        data = connection("Adb", "device")
        data["emulator"] = {"hwnd": 88, "pid": 99}
        runtime = self.runtime(data)
        with patch("app.sessionRuntime.window_process_info", return_value=(99, "emulator.exe")):
            self.assertTrue(runtime._minimize_window_for_task())
        runtime._user32.ShowWindow.assert_called_once_with(88, 7)
        runtime.release_session()
        runtime._user32.SetWindowPlacement.assert_not_called()

    def test_adb_controller_is_created_from_device_metadata_with_lifecycle_sink(self):
        data = connection("Adb", "device")
        with tempfile.NamedTemporaryFile() as adb:
            data["preferences"]["adb_path"] = adb.name
            data["target"]["device"] = {"config": {"value": 1}, "input_methods": 2, "screencap_methods": 3}
            runtime = self.runtime(data)
            with patch("app.sessionRuntime.AdbController") as factory:
                self.assertTrue(runtime._create_controller()[0])
                factory.assert_called_once_with(adb_path=adb.name, address="device", config={"value": 1},
                                                input_methods=2, screencap_methods=3)
            self.assertIsNotNone(runtime._controller_sink_id)

    def test_adb_builtin_exe_launch_is_an_explicit_noop(self):
        runtime = self.runtime(connection("Adb", "device"))
        with patch("app.runtime.subprocess.Popen") as launch:
            self.assertTrue(runtime._launch_program({})[0])
        launch.assert_not_called()

    def test_unsupported_preset_and_invalid_target_are_rejected(self):
        data = snapshot()
        data["resource"]["controller"] = ["Win"]
        with self.assertRaises(ValueError):
            SessionRuntime(data, connection("Adb", "device"), ".")
        self.assertIsNone(target_identity({"target": {"kind": "Win32", "hwnd": True, "pid": 123}}))

    def test_global_input_safety_does_not_serialize_independent_adb(self):
        self.assertFalse(needs_desktop_lock({"type": "Adb"}, True))
        self.assertFalse(needs_desktop_lock(snapshot()["interface"]["controller"][0]))
        self.assertTrue(needs_desktop_lock({"type": "Win32", "win32": {"mouse": "PostMessageWithWindowPos"}}))


class SessionWorkerTests(unittest.TestCase):
    def test_cancel_before_lock_or_init_does_not_create_native_controller(self):
        data = snapshot()
        data["minimize"] = True
        stop = threading.Event()
        stop.set()
        events = Queue()
        with patch("app.multiExecution.SessionRuntime") as runtime:
            session_process(data, connection(), "run", stop, events, threading.Lock())
        runtime.assert_not_called()
        messages = []
        while not events.empty():
            messages.append(events.get())
        result = next(value for kind, value in messages if kind == "result")
        self.assertEqual(result["message"].count("작업이 중지되었습니다."), 1)

    def test_discovery_reuses_declared_presets_and_deduplicates_physical_targets(self):
        data = snapshot()
        data["interface"]["controller"].append({"name": "Win2", "type": "Win32", "win32": {}})
        target = ConnectionTarget("42", "Game", "Win32", hwnd=42, pid=123)
        with patch("app.multiExecution.MonitoringService") as service:
            service.return_value.discover.side_effect = [[target], [], [target]]
            found, errors = discover_connections(data)
        self.assertEqual(len(found), 1)
        self.assertFalse(errors)


if __name__ == "__main__":
    unittest.main()
