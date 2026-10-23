"""Individual discovery keeps free devices without touching real ADB or games."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import test_multi_execution_ui as fixtures
from PySide6.QtTest import QTest


class IndividualAdbDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.MultiExecutionUITests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.MultiExecutionUITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window, self.panel = self.fixture.window, self.fixture.panel
        self.found = [self.fixture.adb_connection(f"127.0.0.1:{16480 + index}") for index in range(4)]
        self.addCleanup(self.wait_for_discovery)

    def wait_for_discovery(self):
        worker = self.panel.discovery_worker
        if worker is not None:
            self.assertTrue(worker.wait(2000))
            fixtures.QApplication.processEvents()

    def add_blank(self, address=""):
        return self.panel.manager.add({"preset": "Adb", "preferences": {"adb_path": "", "address": address}})

    def scan(self, session_id, found=None, errors=None):
        worker = MagicMock(result=(deepcopy(self.found if found is None else found), errors or []), emulators=[])
        self.panel._discovered(worker, session_id, self.window.multi_execution_snapshot(include_tasks=False))

    def choices(self, session_id):
        combo = self.panel.cards[session_id].target_combo
        return [combo.itemData(index) for index in range(combo.count()) if combo.itemData(index) is not None]

    def test_sequential_individual_scans_assign_all_four_distinct_devices(self):
        for index, expected in enumerate(self.found):
            with self.subTest(instance=index + 1):
                sid = self.add_blank()
                self.scan(sid)
                self.assertEqual(self.panel.cards[sid].current_target(), expected["target"])
                self.assertEqual(self.panel.manager.sessions[sid].connection["target"], expected["target"])
                self.assertEqual(self.choices(sid), [c["target"]["key"] for c in self.found[index:]])
        self.assertNotIn("이미 다른 인스턴스", self.window.ui.logPrintText.toPlainText())

    def test_stale_default_address_does_not_select_another_instances_device(self):
        self.panel.manager.add(self.found[0])
        sid = self.add_blank(self.found[0]["target"]["key"])
        self.scan(sid)
        expected = self.found[1]["target"]
        self.assertEqual(self.panel.cards[sid].current_target(), expected)
        self.assertEqual(self.panel.cards[sid].address.text(), expected["key"])
        self.assertEqual(self.panel.manager.sessions[sid].connection["preferences"]["address"], expected["key"])
        self.assertEqual(self.panel.manager.sessions[sid].connection["target"]["device"]["input_methods"], (1 << 64) - 9)

    def test_third_instance_is_available_after_first_two_choose_devices_in_reverse_order(self):
        first = self.add_blank()
        self.scan(first)
        card = self.panel.cards[first]
        card.target_combo.setCurrentIndex(card.target_combo.findData(self.found[1]["target"]["key"]))
        second = self.add_blank()
        self.scan(second)
        third = self.add_blank()
        self.scan(third)
        for sid, expected in ((first, self.found[1]), (second, self.found[0]), (third, self.found[2])):
            self.assertEqual(self.panel.manager.sessions[sid].connection["target"], expected["target"])

    def test_rescan_preserves_current_device_even_when_it_is_not_first(self):
        sid = self.panel.manager.add(self.found[2])
        self.scan(sid)
        self.assertEqual(self.panel.cards[sid].current_target(), self.found[2]["target"])
        self.assertEqual(self.panel.manager.sessions[sid].connection, self.found[2])
        self.assertEqual(len(self.choices(sid)), 4)

    def test_occupied_address_is_excluded_even_with_a_different_controller_preset(self):
        other = deepcopy(self.found[0])
        other["preset"] = "AnotherAdbPreset"
        self.panel.manager.add(other)
        sid = self.add_blank()
        self.scan(sid)
        self.assertEqual(self.panel.cards[sid].current_target(), self.found[1]["target"])
        self.assertNotIn(other["target"]["key"], self.choices(sid))

    def test_single_occupied_device_is_reported_without_resetting_other_instances(self):
        first_id = self.panel.manager.add(self.found[0])
        sid = self.add_blank(self.found[0]["target"]["key"])
        self.scan(sid, self.found[:1])
        self.assertEqual(self.choices(sid), [])
        self.assertIsNone(self.panel.manager.sessions[sid].connection.get("target"))
        self.assertEqual(self.panel.manager.sessions[first_id].connection, self.found[0])
        self.assertIn("사용 가능한 대상이 없습니다", self.window.ui.logPrintText.toPlainText())

    def test_empty_or_failed_scan_preserves_current_device_metadata_and_settings(self):
        expected = deepcopy(self.found[2])
        expected["emulator"] = {"hwnd": 42, "pid": 321, "path": "player.exe", "label": "Player"}
        sid = self.panel.manager.add(expected)
        for errors in ([], ["mocked discovery failure"]):
            with self.subTest(errors=errors):
                self.scan(sid, [], errors)
                self.assertEqual(self.panel.manager.sessions[sid].connection, expected)
                self.assertEqual(self.panel.cards[sid].current_target(), expected["target"])
                self.assertEqual(self.panel.cards[sid].emulator_combo.currentData(), expected["emulator"])

    def test_repeated_scan_excludes_occupied_devices_and_keeps_other_sessions_unchanged(self):
        first = self.panel.manager.add(self.found[0])
        second = self.panel.manager.add(self.found[1])
        sid = self.add_blank()
        self.scan(sid)
        self.scan(sid, list(reversed(self.found)))
        self.assertEqual(self.panel.cards[sid].current_target(), self.found[2]["target"])
        self.assertEqual(set(self.choices(sid)), {c["target"]["key"] for c in self.found[2:]})
        self.assertEqual(self.panel.manager.sessions[first].connection, self.found[0])
        self.assertEqual(self.panel.manager.sessions[second].connection, self.found[1])

    def test_real_discovery_workers_handle_four_manual_instances_without_connecting(self):
        devices = [SimpleNamespace(name=f"Player {index}", **data["target"]["device"])
                   for index, data in enumerate(self.found)]
        with patch("maa.toolkit.Toolkit.init_option", return_value=True), \
                patch("maa.toolkit.Toolkit.find_adb_devices", return_value=devices) as discover, \
                patch("maa.toolkit.Toolkit.find_desktop_windows", return_value=[]), \
                patch("app.monitoring.AdbController") as controller, \
                patch.object(self.panel.manager, "start") as start:
            for expected in self.found:
                sid = self.add_blank()
                self.panel.cards[sid].discover_button.click()
                for _ in range(100):
                    if self.panel.discovery_worker is None:
                        break
                    QTest.qWait(10)
                self.assertIsNone(self.panel.discovery_worker)
                value = self.panel.manager.sessions[sid].connection
                self.assertEqual((value.get("target") or {}).get("key"), expected["target"]["key"])
                self.assertEqual(value["target"]["device"], {**expected["target"]["device"],
                                                              "name": devices[self.found.index(expected)].name})
            self.assertEqual(discover.call_count, 4)
            controller.assert_not_called()
            start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
