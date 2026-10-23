"""Name binding is tested with SDK/window mocks, never real ADB or desktop input."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import test_multi_execution_ui as fixtures
from PySide6.QtTest import QTest

from app.adbWindow import bind_runtime_window, device_name, match_windows
from app.adbStartup import adb_target_descriptor, minimize_adb_window, prepare_adb_connection
from app.monitoring import ConnectionTarget, MonitoringService
from app.sessionRuntime import serialize_target


def target(name="리세 1-SamplePlayer v5+", address="127.0.0.1:16480", *, extras=True):
    return {"kind": "Adb", "key": address, "label": f"{name} · {address}",
            "device": {"name": name, "adb_path": "C:/Player/adb.exe", "address": address,
                       "screencap_methods": 64, "input_methods": (1 << 64) - 9,
                       "config": {"extras": {"sample": {"index": 1}}} if extras else {}}}


def window(label="리세 1", hwnd=88, pid=123):
    return {"hwnd": hwnd, "pid": pid, "path": "C:/Player/Player.exe", "label": label}


class WindowNameMatchingTests(unittest.TestCase):
    def test_sdk_engine_suffix_and_version_are_not_instance_names(self):
        for title in ("리세 1", "리세 1-SamplePlayer", "리세 1-SamplePlayer v5+"):
            with self.subTest(title=title):
                data = target()
                matches, ambiguous = match_windows([data], [window(title)])
                self.assertEqual(matches[data["key"]]["label"], title)
                self.assertTrue(matches[data["key"]]["automatic"])
                self.assertFalse(ambiguous)

    def test_exact_title_supports_case_spacing_and_full_width_digits(self):
        matches, _ = match_windows([target("Player １")], [window("  PLAYER   1 ")])
        self.assertEqual(len(matches), 1)

    def test_instance_one_does_not_match_ten_or_arbitrary_substrings(self):
        for title in ("리세 10", "리세 11", "다른 리세 1", "리세 1 작업"):
            with self.subTest(title=title):
                self.assertEqual(match_windows([target()], [window(title)])[0], {})

    def test_arbitrary_hyphenated_names_without_sdk_extras_are_not_truncated(self):
        self.assertEqual(match_windows([target("A-B", extras=False)], [window("A")])[0], {})
        self.assertEqual(len(match_windows([target("A-B", extras=False)], [window("A-B")])[0]), 1)

    def test_legacy_saved_label_retains_the_sdk_instance_name(self):
        data = target()
        del data["device"]["name"]
        self.assertEqual(len(match_windows([data], [window()])[0]), 1)

    def test_serial_only_and_serial_adb_path_labels_are_not_player_names(self):
        for name in ("127.0.0.1:16480", "127.0.0.1:16480-C:/Player/adb.exe"):
            with self.subTest(name=name):
                self.assertEqual(device_name(target(name)), "")
                self.assertEqual(match_windows([target(name)], [window(name)])[0], {})

    def test_duplicate_titles_are_ambiguous_even_with_different_processes(self):
        data = target()
        matches, ambiguous = match_windows([data], [window(), window(hwnd=89, pid=124)])
        self.assertFalse(matches)
        self.assertEqual(ambiguous, {data["key"]})

    def test_two_devices_cannot_claim_one_window(self):
        first, second = target(), target(address="127.0.0.1:16481")
        matches, ambiguous = match_windows([first, second], [window()])
        self.assertFalse(matches)
        self.assertEqual(ambiguous, {first["key"], second["key"]})

    def test_reservations_invalid_handles_and_client_process_are_excluded(self):
        data = target()
        for candidates, kwargs in (([window()], {"reserved": [window()]}),
                                   ([window()], {"excluded_pids": [123]}),
                                   ([window(hwnd=0)], {}), ([window(pid=0)], {}),
                                   ([window(label="")], {})):
            with self.subTest(candidates=candidates, kwargs=kwargs):
                self.assertEqual(match_windows([data], candidates, **kwargs)[0], {})

    def test_duplicate_records_for_one_window_are_not_multiple_candidates(self):
        self.assertEqual(len(match_windows([target()], [window(), window()])[0]), 1)


class RuntimeNameBindingTests(unittest.TestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(connection={"target": target(), "preferences": {}, "emulator": {}},
                                       log_sink=MagicMock(), _user32=MagicMock())
        self.runtime._user32.IsIconic.return_value = True
        patcher = patch("app.adbWindow.desktop_windows", return_value=[window()])
        self.windows = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch("app.monitoring.window_process_info", return_value=(123, window()["path"]))
        self.process = patcher.start()
        self.addCleanup(patcher.stop)

    def test_existing_player_is_bound_and_minimized_once_without_manual_selection(self):
        self.assertTrue(minimize_adb_window(self.runtime))
        self.assertTrue(minimize_adb_window(self.runtime))
        self.runtime._user32.ShowWindow.assert_called_once_with(88, 7)
        self.assertTrue(self.runtime.connection["emulator"]["automatic"])
        self.runtime._user32.MoveWindow.assert_not_called()
        self.runtime._user32.SetWindowPlacement.assert_not_called()
        self.assertEqual(sum("최소화했습니다" in call.args[0]
                             for call in self.runtime.log_sink._emit.call_args_list), 1)

    def test_main_runtime_uses_execution_connection_not_session_only_attribute(self):
        self.runtime._adb_execution_connection = self.runtime.connection
        del self.runtime.connection
        self.assertTrue(minimize_adb_window(self.runtime))
        self.runtime._user32.ShowWindow.assert_called_once_with(88, 7)
        self.assertEqual(self.runtime._adb_execution_connection["emulator"]["label"], "리세 1")

    def test_explicit_manual_choice_is_not_overridden_by_name_binding(self):
        chosen = window("별도 선택 창", hwnd=99)
        self.runtime.connection["emulator"] = chosen
        self.assertEqual(bind_runtime_window(self.runtime), chosen)
        self.windows.assert_not_called()
        self.assertTrue(minimize_adb_window(self.runtime))
        self.runtime._user32.ShowWindow.assert_called_once_with(99, 7)

    def test_missing_or_ambiguous_window_skips_input_and_reports_once(self):
        for candidates in ([], [window(), window(hwnd=89, pid=124)]):
            with self.subTest(candidates=candidates):
                self.windows.return_value = candidates
                self.runtime.connection = {"target": target(), "emulator": {}}
                self.runtime._adb_minimize_skipped = False
                self.runtime.log_sink.reset_mock()
                self.assertTrue(minimize_adb_window(self.runtime))
                self.assertTrue(minimize_adb_window(self.runtime))
                self.runtime._user32.ShowWindow.assert_not_called()
                self.assertEqual(sum("생략합니다" in call.args[0]
                                     for call in self.runtime.log_sink._emit.call_args_list), 1)

    def test_changed_process_is_not_minimized(self):
        self.process.return_value = (999, "C:/Other.exe")
        self.assertFalse(minimize_adb_window(self.runtime))
        self.runtime._user32.ShowWindow.assert_not_called()

    def test_worker_respects_other_sessions_window_and_parent_gui_reservations(self):
        for snapshot in ({"reserved_windows": [window()]}, {"client_pid": 123}):
            with self.subTest(snapshot=snapshot):
                self.runtime.snapshot = snapshot
                self.assertEqual(bind_runtime_window(self.runtime), {})
                self.runtime._user32.ShowWindow.assert_not_called()

    def test_stale_automatic_window_is_discarded_for_unnamed_or_ambiguous_target(self):
        for data in (target("127.0.0.1:16480"), {**target(), "_window_ambiguous": True}):
            with self.subTest(data=data):
                self.runtime.connection = {"target": data, "emulator": {**window(), "automatic": True}}
                self.assertEqual(bind_runtime_window(self.runtime), {})
                self.assertEqual(self.runtime.connection["emulator"], {})
                self.windows.assert_not_called()

    def test_sdk_name_and_binding_survive_preview_serialization_and_known_target(self):
        data = target()
        data["device"]["adb_path"] = str(Path(__file__))
        connection = {"target": data, "emulator": {**window(), "automatic": True}}
        descriptor = adb_target_descriptor(connection)
        serialized = serialize_target(descriptor)
        restored = prepare_adb_connection({"address": data["key"], "adb_path": str(Path(__file__))}, serialized)
        self.assertEqual(restored["target"]["device"]["name"], data["device"]["name"])
        self.assertEqual(restored["target"]["device"]["input_methods"], (1 << 64) - 9)
        self.assertEqual(restored["emulator"], connection["emulator"])

    def test_overridden_adb_path_does_not_reuse_a_stale_named_target(self):
        known = target()
        known["emulator"] = window()
        result = prepare_adb_connection({"address": known["key"], "adb_path": "C:/Other/adb.exe"}, known)
        self.assertNotIn("device", result["target"])
        self.assertEqual(result["emulator"], {})

    def test_preflight_connect_binds_the_player_but_never_minimizes_or_posts_input(self):
        data = target()
        device = SimpleNamespace(**data["device"])
        descriptor = ConnectionTarget(data["key"], data["label"], "Adb", device=device)
        service = MonitoringService({"controller": [{"name": "Adb", "type": "Adb"}]}, {}, ".")
        self.addCleanup(service.close)
        service.targets, service._discovered_preset = [descriptor], "Adb"
        controller = MagicMock(connected=True)
        controller.post_connection.return_value.wait.return_value.succeeded = True
        with patch("app.monitoring.Toolkit.init_option", return_value=True), \
                patch("app.monitoring.AdbController", return_value=controller):
            result = service.connect("Adb", data["key"], {"address": data["key"]})
        self.assertEqual(result.emulator["label"], "리세 1")
        self.assertEqual(service.targets[0], result)
        self.runtime._user32.ShowWindow.assert_not_called()
        controller.post_click.assert_not_called()

    def test_preflight_device_name_collision_remains_blocked_in_runtime_handoff(self):
        data = target()
        other = target(address="127.0.0.1:16481")
        service = MonitoringService({"controller": [{"name": "Adb", "type": "Adb"}]}, {}, ".")
        self.addCleanup(service.close)
        service.targets = [ConnectionTarget(item["key"], item["label"], "Adb", device=SimpleNamespace(**item["device"]))
                           for item in (data, other)]
        service._discovered_preset = "Adb"
        controller = MagicMock(connected=True)
        controller.post_connection.return_value.wait.return_value.succeeded = True
        with patch("app.monitoring.Toolkit.init_option", return_value=True), \
                patch("app.monitoring.AdbController", return_value=controller):
            result = service.connect("Adb", data["key"], {"address": data["key"]})
        self.assertIsNone(result.emulator)
        self.assertTrue(serialize_target(result)["_window_ambiguous"])


class NameBindingUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MultiExecutionUITests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.MultiExecutionUITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window, self.panel = self.fixture.window, self.fixture.panel
        self.found = [self.fixture.adb_connection(f"127.0.0.1:{16480 + i}") for i in (1, 10)]
        for data, i in zip(self.found, (1, 10)):
            data["target"]["device"]["name"] = f"리세 {i}-SamplePlayer v5+"
            data["target"]["label"] = data["target"]["device"]["name"] + " · " + data["target"]["key"]
        self.windows = [window("리세 1", 88, 123), window("리세 10", 99, 124)]

    def scan(self, sid):
        worker = MagicMock(result=(deepcopy(self.found), []), emulators=deepcopy(self.windows))
        self.panel._discovered(worker, sid, self.window.multi_execution_snapshot(include_tasks=False))

    def test_individual_discovery_selects_the_matching_window_with_details_collapsed(self):
        sid = self.panel.manager.add({"preset": "Adb"})
        self.scan(sid)
        card = self.panel.cards[sid]
        self.assertFalse(card.adb_details.isVisible())
        self.assertEqual(card.emulator_combo.currentText(), "리세 1")
        self.assertEqual(self.panel.manager.sessions[sid].connection["emulator"]["hwnd"], 88)
        card.target_combo.setCurrentIndex(2)
        self.assertEqual(card.emulator_combo.currentText(), "리세 10")
        self.assertEqual(self.panel.manager.sessions[sid].connection["emulator"]["hwnd"], 99)

    def test_manual_override_is_kept_when_rediscovering_the_same_device(self):
        sid = self.panel.manager.add({"preset": "Adb"})
        self.scan(sid)
        card = self.panel.cards[sid]
        index = next(i for i in range(card.emulator_combo.count())
                     if (card.emulator_combo.itemData(i) or {}).get("hwnd") == 99)
        card.emulator_combo.setCurrentIndex(index)
        self.scan(sid)
        self.assertEqual(card.emulator_combo.currentText(), "리세 10")
        self.assertFalse(card.emulator_combo.currentData().get("automatic", False))

    def test_manually_selecting_a_former_automatic_item_is_an_explicit_override(self):
        sid = self.panel.manager.add({"preset": "Adb"})
        self.scan(sid)
        card = self.panel.cards[sid]
        card.target_combo.setCurrentIndex(2)
        index = next(i for i in range(card.emulator_combo.count())
                     if (card.emulator_combo.itemData(i) or {}).get("hwnd") == 88)
        card.emulator_combo.setCurrentIndex(index)
        self.assertFalse(self.panel.manager.sessions[sid].connection["emulator"].get("automatic", False))
        self.scan(sid)
        self.assertEqual(card.emulator_combo.currentText(), "리세 1")

    def test_typing_another_address_clears_the_previous_automatic_window(self):
        sid = self.panel.manager.add({"preset": "Adb"})
        self.scan(sid)
        card = self.panel.cards[sid]
        self.assertTrue(card.emulator_combo.currentData()["automatic"])
        card.address.setText("127.0.0.1:19999")
        card._configure()
        self.assertEqual(self.panel.manager.sessions[sid].connection["emulator"], {})
        self.assertEqual(card.emulator_combo.currentIndex(), 0)

    def test_individual_name_collisions_remain_blocked_when_handed_to_worker(self):
        self.found[1]["target"]["device"]["name"] = self.found[0]["target"]["device"]["name"]
        self.found[1]["target"]["label"] = self.found[0]["target"]["label"]
        self.windows = self.windows[:1]
        sid = self.panel.manager.add({"preset": "Adb"})
        self.scan(sid)
        self.assertEqual(self.panel.manager.sessions[sid].connection["emulator"], {})
        self.assertTrue(self.panel.manager.sessions[sid].connection["target"]["_window_ambiguous"])

    def test_global_discovery_binds_each_device_before_connection_checks(self):
        desktop = [SimpleNamespace(hwnd=w["hwnd"], window_name=w["label"]) for w in self.windows]
        with patch("app.multiExecutionUI.discover_connections", return_value=(deepcopy(self.found), [])), \
                patch("maa.toolkit.Toolkit.find_desktop_windows", return_value=desktop), \
                patch("app.multiExecutionUI.window_process_info", side_effect=lambda hwnd: (123 if hwnd == 88 else 124, "C:/Player/Player.exe")), \
                patch.object(self.panel, "start_sessions") as start:
            self.panel.auto_button.click()
            for _ in range(100):
                if self.panel.discovery_worker is None:
                    break
                QTest.qWait(10)
            self.assertIsNone(self.panel.discovery_worker)
        sessions = list(self.panel.manager.sessions.values())
        self.assertEqual([s.connection["emulator"]["hwnd"] for s in sessions], [88, 99])
        for session in sessions:
            card = self.panel.cards[session.id]
            self.assertEqual(card.emulator_combo.count(), 3)
            self.assertEqual(card.emulator_combo.currentData(), session.connection["emulator"])
        start.assert_called_once()

    def test_check_result_updates_the_card_and_persisted_binding(self):
        sid = self.panel.manager.add(self.found[0])
        snapshot = self.window.multi_execution_snapshot(include_tasks=False)
        self.panel.manager.start(sid, snapshot, mode="check")
        session = self.panel.manager.sessions[sid]
        session.events.put(("result", {"succeeded": True, "message": "connected",
                                      "emulator": {**self.windows[0], "automatic": True}}))
        session.process.alive = False
        self.panel.manager.poll()
        self.assertEqual(self.panel.cards[sid].emulator_combo.currentText(), "리세 1")
        self.assertEqual(self.panel.manager.saved_sessions()[0]["connection"]["emulator"]["hwnd"], 88)

    def test_missing_binding_result_clears_a_stale_automatic_selection_in_the_card(self):
        data = deepcopy(self.found[0])
        data["emulator"] = {**self.windows[0], "automatic": True}
        sid = self.panel.manager.add(data)
        self.panel.manager.start(sid, self.window.multi_execution_snapshot(include_tasks=False), mode="check")
        session = self.panel.manager.sessions[sid]
        session.events.put(("result", {"succeeded": True, "message": "connected", "emulator": {}}))
        session.process.alive = False
        self.panel.manager.poll()
        self.assertEqual(self.panel.cards[sid].emulator_combo.currentIndex(), 0)
        self.assertEqual(session.connection["emulator"], {})

    def test_main_task_keeps_preflight_name_and_bound_window_when_handing_off_to_runtime(self):
        data = self.found[0]["target"]
        descriptor = ConnectionTarget(data["key"], data["label"], "Adb", device=SimpleNamespace(
            **{**data["device"], "address": data["key"]}), emulator={**self.windows[0], "automatic": True})
        self.window.monitor.targets = [descriptor]
        self.window.monitor.preferences["address"] = data["key"]
        program = {"controller": {"name": "Adb", "type": "Adb"},
                   "connection": {"address": data["key"], "adb_path": data["device"]["adb_path"]}}
        try:
            with patch.object(self.window.monitor, "prepare_task_start", return_value=False), \
                    patch.object(self.window.settings_panel, "program_settings", return_value=program), \
                    patch.object(self.window.settings_panel, "controller_settings", return_value=program["controller"]), \
                    patch.object(self.window, "build_execution_queue", return_value=[("Login_Main", {})]), \
                    patch("app.winUI.RuntimeWorker") as worker:
                self.window.on_task_start()
                known = worker.call_args.kwargs["program_settings"]["_adb_target"]
                self.assertEqual(known["device"]["name"], data["device"]["name"])
                self.assertEqual(known["emulator"]["hwnd"], 88)
        finally:
            self.window.worker = None
            self.window.isRunning = False


if __name__ == "__main__":
    unittest.main()
