"""Discovery selection and task preflight share one saved ADB address, no device IO."""
import json
from unittest.mock import MagicMock, patch
import unittest

import test_runtime_lifecycle as fixtures
from app.adbStartup import prepare_adb_connection
from app.monitoring import ConnectionTarget


class AdbDiscoveryPreferencesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.monitor = self.window.monitor
        self.settings = self.window.settings_panel
        preset = {"name": "Adb", "type": "Adb", "label": "ADB"}
        self.window.runtime.interface["controller"].append(preset)
        self.window.runtime.resource_config["controller"].append("Adb")
        self.settings.controllers.append(preset)
        self.settings.controller_combo.addItem("ADB", preset)
        self.monitor.service.presets.append(preset)
        self.monitor.select_preset("Adb")
        self.adb = self.window.runtime.user_dir / "adb.exe"
        self.adb.touch()
        self.monitor.change_preferences({"adb_path": str(self.adb), "address": ""})
        self.first = ConnectionTarget("127.0.0.1:16480", "First emulator", "Adb")
        self.second = ConnectionTarget("127.0.0.1:16384", "Second emulator", "Adb")

    def discover(self, targets):
        with patch.object(self.monitor.service, "discover", return_value=targets):
            self.monitor.discover()
            self.fixture.wait_for_monitor()

    def saved_address(self):
        data = json.loads(self.settings.store.config_path.read_text(encoding="utf-8"))
        return data["connection"]["address"]

    def test_first_auto_selected_address_reaches_settings_panels_and_task_preflight(self):
        self.discover([self.first, self.second])
        self.assertEqual(self.monitor.target_key, self.first.key)
        self.assertEqual(self.monitor.preferences["address"], self.first.key)
        self.assertEqual(self.saved_address(), self.first.key)
        for panel in self.monitor.panels:
            self.assertEqual(panel.address.text(), self.first.key)
        with patch("app.adbStartup.Toolkit.find_adb_devices") as devices:
            connection = prepare_adb_connection(self.settings.program_settings()["connection"])
        devices.assert_not_called()
        self.assertEqual(connection["target"]["key"], self.first.key)
        with patch.object(self.monitor.service, "connect", return_value=self.first) as connect:
            self.monitor.connect_target()
            self.fixture.wait_for_monitor()
        self.assertEqual(connect.call_args.args[1], self.first.key)
        self.assertEqual(connect.call_args.args[2]["address"], self.first.key)

    def test_rediscovery_saves_even_if_target_key_was_already_auto_selected(self):
        self.monitor.target_key = self.first.key
        self.discover([self.first])
        self.assertEqual(self.saved_address(), self.first.key)

    def test_manual_target_change_uses_the_same_persistence_path(self):
        self.discover([self.first, self.second])
        self.monitor.select_target(self.second.key)
        self.assertEqual(self.saved_address(), self.second.key)
        self.assertEqual(self.monitor.preferences["adb_path"], str(self.adb))

    def test_empty_discovery_does_not_erase_a_manual_address(self):
        self.monitor.change_preferences({"adb_path": str(self.adb), "address": self.first.key})
        self.discover([])
        self.assertEqual(self.monitor.target_key, "")
        self.assertEqual(self.saved_address(), self.first.key)

    def test_win32_discovery_does_not_overwrite_adb_preferences(self):
        self.monitor.change_preferences({"adb_path": str(self.adb), "address": self.first.key})
        self.monitor.select_preset("Win32PrintWindow")
        self.discover([ConnectionTarget("42", "Window", "Win32", hwnd=42)])
        self.assertEqual(self.saved_address(), self.first.key)

    def test_stale_discovery_does_not_save_an_old_target(self):
        worker = MagicMock(outcome=(True, [self.first]))
        self.monitor.worker = worker
        self.monitor._finished(worker, "discover", self.monitor.generation - 1)
        self.assertEqual(self.monitor.preferences["address"], "")
        self.assertEqual(self.saved_address(), "")


if __name__ == "__main__":
    unittest.main()
