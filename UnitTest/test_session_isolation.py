"""Disconnect/crash isolation without a real ADB, game, native fault or power action."""
from copy import deepcopy
import multiprocessing as mp
import os
from queue import Empty
import threading
import time
import unittest
from unittest.mock import MagicMock, PropertyMock, patch

import test_runtime_lifecycle as fixtures
from test_multi_execution import FakeContext, FakeQueue, connection, snapshot
from app.adbPolicy import shared_adb_options
from app.multiExecution import SessionManager
from app.sessionIPC import SessionEventInbox, SharedFlag
from app.sessionRuntime import SessionRuntime


def exit_during_session(sender, flag):
    # An intentional exit status, not an access violation/Windows error popup.
    sender.put(("log", "before exit"), timeout=1)
    while not flag.is_set():
        time.sleep(0.005)
    os._exit(7)


class SharedAdbPolicyTests(unittest.TestCase):
    def test_every_controller_uses_client_only_noop_instead_of_kill_server(self):
        options = {"adb_path": "adb.exe", "address": "127.0.0.1:5555"}
        self.assertEqual(shared_adb_options(options)["config"]["command"]["KillServer"], ["{ADB}", "version"])
        self.assertNotIn("config", options)

    def test_other_options_and_custom_commands_remain_without_mutating_discovery(self):
        original = {"config": {"other": [1], "command": {"Monitor": ["custom"], "KillServer": ["danger"]}},
                    "input_methods": 123, "screencap_methods": 456}
        saved = deepcopy(original)
        actual = shared_adb_options(original)
        self.assertEqual(original, saved)
        self.assertEqual(actual["config"]["command"]["Monitor"], ["custom"])
        self.assertEqual(actual["config"]["other"], [1])
        self.assertEqual(actual["input_methods"], 123)
        self.assertEqual(actual["screencap_methods"], 456)
        self.assertNotIn("kill-server", repr(actual))

    def test_malformed_config_is_not_silently_replaced(self):
        for options in ({"config": []}, {"config": {"command": []}}):
            with self.assertRaises(ValueError):
                shared_adb_options(options)


class SessionFailureIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.manager = SessionManager(context=FakeContext(), event_channel_factory=FakeQueue)
        self.addCleanup(self.manager.deleteLater)
        self.addCleanup(self.manager.timer.stop)

    def start(self, key, *, win32=False):
        data = snapshot()
        data["minimize"] = win32
        sid = self.manager.add(connection("Win32" if win32 else "Adb", key))
        self.manager.start(sid, data)
        self.manager.timer.stop()
        return self.manager.sessions[sid]

    def test_disconnect_stops_only_its_session_and_keeps_the_other_running(self):
        a, b = self.start("device-a"), self.start("device-b")
        a.events.put(("disconnected", "대상 연결 해제"))
        self.manager.poll()
        self.assertTrue(a.stop_event.is_set())
        self.assertEqual(a.state, "stopping")
        self.assertFalse(b.stop_event.is_set())
        self.assertTrue(b.active)
        a.process.alive = False
        self.manager.poll()
        self.assertEqual(a.state, "failed")
        self.assertIn("대상 연결 해제", a.result["message"])
        self.assertTrue(b.active)

    def test_failed_cleanup_timeout_terminates_only_the_owned_failed_worker(self):
        a, b = self.start("device-a"), self.start("device-b")
        process = a.process
        a.events.put(("disconnected", "대상 연결 해제"))
        with patch("app.multiExecution.time.monotonic", return_value=10):
            self.manager.poll()
        self.assertFalse(process.terminated)
        with patch("app.multiExecution.time.monotonic", return_value=19):
            self.manager.poll()
        self.assertTrue(process.terminated)
        self.assertFalse(b.process.terminated)
        self.assertFalse(b.stop_event.is_set())
        self.manager.poll()
        self.assertFalse(a.active)
        self.assertEqual(a.state, "failed")
        self.assertTrue(b.active)

    def test_manual_stop_has_no_force_termination_deadline(self):
        a = self.start("device-a")
        self.manager.stop(a.id)
        with patch("app.multiExecution.time.monotonic", return_value=100000):
            self.manager.poll()
        self.assertFalse(a.process.terminated)
        self.assertEqual(a.state, "stopping")

    def test_failed_result_with_stuck_native_destructor_has_a_cleanup_deadline(self):
        a, b = self.start("device-a"), self.start("device-b")
        a.events.put(("result", {"succeeded": False, "message": "controller cleanup failed"}))
        with patch("app.multiExecution.time.monotonic", return_value=10):
            self.manager.poll()
        self.assertTrue(a.stop_event.is_set())
        with patch("app.multiExecution.time.monotonic", return_value=19):
            self.manager.poll()
        self.assertTrue(a.process.terminated)
        self.assertFalse(b.process.terminated)
        self.assertFalse(b.stop_event.is_set())

    def test_terminal_success_cannot_leave_a_hung_child_forever(self):
        a, b = self.start("device-a"), self.start("device-b")
        a.events.put(("result", {"succeeded": True, "message": "done"}))
        with patch("app.multiExecution.time.monotonic", return_value=10):
            self.manager.poll()
        self.assertFalse(a.process.terminated)
        with patch("app.multiExecution.time.monotonic", return_value=19):
            self.manager.poll()
        self.assertTrue(a.process.terminated)
        self.assertFalse(b.stop_event.is_set())
        self.manager.poll()
        self.assertEqual(a.state, "failed")
        self.assertTrue(b.active)

    def test_cancelled_result_keeps_the_manual_stop_no_force_policy(self):
        a = self.start("device-a")
        self.manager.stop(a.id)
        a.events.put(("result", {"succeeded": False, "message": "cancelled"}))
        with patch("app.multiExecution.time.monotonic", return_value=1):
            self.manager.poll()
        with patch("app.multiExecution.time.monotonic", return_value=100000):
            self.manager.poll()
        self.assertFalse(a.process.terminated)

    def test_bad_pipe_does_not_prevent_another_session_finishing(self):
        for error in (EOFError("eof"), OSError("broken pipe"), ValueError("closed"), TypeError("bad message")):
            with self.subTest(error=error):
                a = self.start(str(id(error)))
                b = self.start("next-" + str(id(error)))
                a.events.get_nowait = MagicMock(side_effect=error)
                b.events.put(("result", {"succeeded": True, "message": "done"}))
                b.process.alive = False
                self.manager.poll()
                self.assertEqual(b.state, "completed")
                self.assertTrue(a.stop_event.is_set())
                self.assertFalse(b.stop_event.is_set())
                a.process.alive = False
                self.manager.poll()
                self.assertEqual(a.state, "failed")

    def test_bad_result_is_contained_and_does_not_run_global_completion_actions(self):
        a, b = self.start("device-a"), self.start("device-b")
        a.events.put(("result", {"succeeded": "yes"}))
        a.process.alive = False
        b.events.put(("result", {"succeeded": True, "message": "done"}))
        b.process.alive = False
        waves = []
        self.manager.wave_finished.connect(lambda results, actions: waves.append(results))
        self.manager.poll()
        self.assertEqual(a.state, "failed")
        self.assertEqual(b.state, "completed")
        self.assertEqual(len(waves), 1)
        self.assertFalse(all(result["succeeded"] for result in waves[0]))

    def test_malformed_state_cannot_break_the_other_sessions_poll(self):
        a, b = self.start("device-a"), self.start("device-b")
        a.events.put(("state", {"unexpected": "payload"}))
        b.events.put(("result", {"succeeded": True, "message": "done"}))
        b.process.alive = False
        self.manager.poll()
        self.assertEqual(b.state, "completed")
        self.assertTrue(a.active)
        self.assertFalse(a.stop_event.is_set())

    def test_crashed_win32_owner_cannot_strand_the_next_input_session(self):
        a, b = self.start(42, win32=True), self.start(43, win32=True)
        self.assertTrue(a.desktop_lease.ready.is_set())
        self.assertFalse(b.desktop_lease.ready.is_set())
        a.process.exitcode = 0xc0000005
        a.process.alive = False
        self.manager.poll()
        self.assertEqual(a.state, "failed")
        self.assertEqual(self.manager.desktop_owner, b.id)
        self.assertTrue(b.desktop_lease.ready.is_set())
        self.assertFalse(b.stop_event.is_set())

    def test_delayed_receiver_after_exit_never_blocks_other_sessions(self):
        a, b = self.start("device-a"), self.start("device-b")
        a.process.alive = False
        a.events.reader_done = False
        b.events.put(("result", {"succeeded": True, "message": "done"}))
        b.process.alive = False
        with patch("app.multiExecution.time.monotonic", return_value=1):
            self.manager.poll()
        self.assertTrue(a.active)
        self.assertEqual(b.state, "completed")
        with patch("app.multiExecution.time.monotonic", return_value=3):
            self.manager.poll()
        self.assertEqual(a.state, "failed")

    def test_receiver_start_failure_stops_only_its_started_child_asynchronously(self):
        a = self.start("device-a")
        sid = self.manager.add(connection("Adb", "device-b"))
        with patch.object(FakeQueue, "start", side_effect=RuntimeError("cannot start reader")):
            with self.assertRaisesRegex(RuntimeError, "cannot start reader"):
                self.manager.start(sid, snapshot())
        b = self.manager.sessions[sid]
        self.assertTrue(b.active)
        self.assertTrue(b.stop_event.is_set())
        self.assertFalse(a.stop_event.is_set())
        b.process.alive = False
        self.manager.poll()
        self.assertEqual(b.state, "failed")
        self.assertTrue(a.active)


class SessionIPCIsolationTests(unittest.TestCase):
    def inbox(self):
        inbox = SessionEventInbox(mp.get_context("spawn"))
        self.addCleanup(inbox.close)
        return inbox

    def test_slow_or_partial_native_read_does_not_block_gui_queue_access(self):
        inbox = self.inbox()
        release, entered = threading.Event(), threading.Event()
        def partial_read():
            entered.set()
            release.wait(2)
            raise EOFError
        with patch.object(inbox._reader, "recv", side_effect=partial_read):
            inbox.start()
            try:
                self.assertTrue(entered.wait(1))
                started = time.monotonic()
                with self.assertRaises(Empty):
                    inbox.get_nowait()
                self.assertLess(time.monotonic() - started, 0.05)
            finally:
                release.set()
                inbox._thread.join(2)
        self.assertTrue(inbox.reader_done)

    def test_invalid_native_frame_is_confined_to_one_receiver(self):
        broken, healthy = self.inbox(), self.inbox()
        broken.start()
        healthy.start()
        broken.sender.connection.send_bytes(b"invalid pickle frame")
        broken.close_sender()
        healthy.sender.put(("result", {"succeeded": True, "message": "done"}))
        healthy.close_sender()
        broken._thread.join(2)
        healthy._thread.join(2)
        self.assertTrue(broken.error)
        self.assertEqual(healthy.get_nowait()[1]["message"], "done")
        self.assertFalse(healthy.error)

    def test_start_failure_closes_the_unstarted_receiver_handle(self):
        inbox = self.inbox()
        with patch("app.sessionIPC.threading.Thread.start", side_effect=RuntimeError("thread limit")):
            with self.assertRaisesRegex(RuntimeError, "thread limit"):
                inbox.start()
        inbox.close()
        self.assertTrue(inbox.reader_done)
        self.assertTrue(inbox._reader.closed)

    def test_log_flood_cannot_evict_disconnect_or_terminal_result(self):
        inbox = self.inbox()
        inbox.start()
        inbox.sender.put(("disconnected", "offline"))
        inbox.sender.put(("result", {"succeeded": False, "message": "failed"}))
        for index in range(700):
            inbox.sender.put(("log", str(index)))
        inbox.close_sender()
        inbox._thread.join(3)
        self.assertTrue(inbox.reader_done)
        self.assertEqual(inbox.get_nowait(), ("disconnected", "offline"))
        records = []
        while True:
            try:
                records.append(inbox.get_nowait())
            except Empty:
                break
        self.assertLessEqual(len(records), 513)
        self.assertEqual(records[-1][0], "result")

    def test_spawned_worker_exit_closes_its_pipe_and_shared_flag_without_deadlock(self):
        context = mp.get_context("spawn")
        inbox = self.inbox()
        flag = SharedFlag(context)
        process = context.Process(target=exit_during_session, args=(inbox.sender, flag))
        process.start()
        try:
            inbox.close_sender()
            inbox.start()
            flag.set()
            process.join(10)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 7)
            inbox._thread.join(2)
            self.assertTrue(inbox.reader_done)
            self.assertEqual(inbox.get_nowait(), ("log", "before exit"))
            self.assertTrue(flag.is_set())
        finally:
            if process.is_alive():
                process.terminate()
                process.join(3)
            process.close()


class ConnectionWatchTests(unittest.TestCase):
    def runtime(self):
        with patch("app.runtime.create_user32", return_value=MagicMock()):
            runtime = SessionRuntime(snapshot(), connection("Adb", "device"), ".")
        self.addCleanup(runtime.stop_connection_watch)
        return runtime

    def test_disconnected_sdk_monitor_reports_once_without_posting_input_or_captures(self):
        runtime = self.runtime()
        controller = runtime.controller = MagicMock(connected=False)
        lost = threading.Event()
        reported = MagicMock(side_effect=lambda _: lost.set())
        with patch("app.sessionRuntime.CONNECTION_CHECK_INTERVAL_SECONDS", 0.005):
            runtime.start_connection_watch(reported)
            self.assertTrue(lost.wait(1))
            runtime.stop_connection_watch()
        reported.assert_called_once()
        controller.post_screencap.assert_not_called()
        controller.post_connection.assert_not_called()
        controller.post_inactive.assert_not_called()

    def test_one_transient_failure_is_not_a_disconnect(self):
        runtime = self.runtime()
        controller = runtime.controller = MagicMock()
        type(controller).connected = PropertyMock(side_effect=[False, True, False, True, True, True])
        reported = MagicMock()
        with patch("app.sessionRuntime.CONNECTION_CHECK_INTERVAL_SECONDS", 0.005):
            runtime.start_connection_watch(reported)
            time.sleep(0.022)
            runtime.stop_connection_watch()
        reported.assert_not_called()

    def test_cleanup_stops_connection_watch_before_controller_destruction(self):
        runtime = self.runtime()
        runtime.controller = MagicMock(connected=True)
        with patch("app.sessionRuntime.CONNECTION_CHECK_INTERVAL_SECONDS", 0.005):
            runtime.start_connection_watch(MagicMock())
            watcher = runtime._connection_watch
            with patch("app.runtime.AppRuntime.release_session", return_value=(True, "released")) as release:
                def check():
                    self.assertFalse(watcher.is_alive())
                    return True, "released"
                release.side_effect = check
                runtime.release_session()
        self.assertIsNone(runtime._connection_watch)


if __name__ == "__main__":
    unittest.main()
