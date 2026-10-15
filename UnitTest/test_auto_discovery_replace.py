"""Replace only the idle global discovery list; all native operations are mocked."""
from copy import deepcopy
import json
import unittest
from unittest.mock import MagicMock, patch

import test_multi_execution_ui as ui_fixtures
from test_multi_execution import connection
from PySide6.QtTest import QTest


class AutoDiscoveryReplacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui_fixtures.MultiExecutionUITests.setUpClass()

    def setUp(self):
        self.fixture = ui_fixtures.MultiExecutionUITests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.panel, self.window = self.fixture.panel, self.fixture.window
        self.addCleanup(self.wait_for_discovery)

    def wait_for_discovery(self):
        worker = self.panel.discovery_worker
        if worker is not None:
            self.assertTrue(worker.wait(2000))
            ui_fixtures.QApplication.processEvents()

    def snapshot(self):
        return self.window.multi_execution_snapshot(include_tasks=False)

    def apply_global_results(self, found, errors=None):
        worker = MagicMock(result=(found, errors or []), emulators=[])
        with patch.object(self.panel, "start_sessions") as start:
            self.panel._discovered(worker, None, self.snapshot())
        return start

    def assert_targets(self, expected):
        sessions = list(self.panel.manager.sessions.values())
        self.assertEqual([session.connection for session in sessions], expected)
        self.assertEqual(set(self.panel.cards), set(self.panel.manager.sessions))
        self.assertEqual(self.panel.cards_layout.count(), len(expected) + 1)
        for session in sessions:
            card = self.panel.cards[session.id]
            self.assertEqual(card.current_target(), session.connection["target"])
            choices = [card.target_combo.itemData(index) for index in range(card.target_combo.count())
                       if card.target_combo.itemData(index) is not None]
            self.assertEqual(choices, [session.connection["target"]["key"]])

    def test_global_results_replace_configured_and_blank_instances(self):
        self.fixture.add(42)
        self.panel.manager.add({"preset": self.panel.preferred_preset()})
        old_cards = dict(self.panel.cards)
        found = [self.fixture.adb_connection(), self.fixture.adb_connection("127.0.0.1:16384")]
        start = self.apply_global_results(found)
        self.assert_targets(found)
        self.assertTrue(set(old_cards).isdisjoint(self.panel.cards))
        self.assertTrue(all(card.isHidden() for card in old_cards.values()))
        self.assertEqual([session.title for session in self.panel.manager.sessions.values()],
                         ["인스턴스 1", "인스턴스 2"])
        start.assert_called_once()
        self.assertEqual(start.call_args.args[0], list(self.panel.manager.sessions))
        self.assertEqual(start.call_args.args[1], "check")

    def test_repeated_global_results_do_not_accumulate_old_or_duplicate_targets(self):
        self.fixture.add(42)
        first, second = self.fixture.adb_connection(), self.fixture.adb_connection("127.0.0.1:16384")
        self.apply_global_results([first, first, second])
        self.assert_targets([first, second])
        old_ids = set(self.panel.manager.sessions)
        self.apply_global_results([second])
        self.assert_targets([second])
        self.assertTrue(old_ids.isdisjoint(self.panel.manager.sessions))

    def test_no_discovered_targets_removes_all_previous_instances(self):
        self.fixture.add(42)
        self.panel.manager.add({"preset": self.panel.preferred_preset()})
        start = self.apply_global_results([], ["mocked discovery error"])
        self.assertFalse(self.panel.manager.sessions)
        self.assertFalse(self.panel.cards)
        self.assertEqual(self.panel.cards_layout.count(), 1)
        start.assert_not_called()
        self.assertIn("대상 0개 확인", self.window.ui.logPrintText.toPlainText())
        self.assertIn("mocked discovery error", self.window.ui.logPrintText.toPlainText())

    def test_individual_target_discovery_keeps_other_instances_and_ids(self):
        first, second = self.fixture.add(42), self.fixture.add(43)
        other = deepcopy(self.panel.manager.sessions[second].connection)
        data = connection(value=44)
        data["preset"] = "Win32PrintWindow"
        worker = MagicMock(result=([data], []), emulators=[])
        with patch.object(self.panel, "start_sessions") as start:
            self.panel._discovered(worker, first, self.snapshot())
        self.assertEqual(set(self.panel.cards), {first, second})
        self.assertEqual(self.panel.cards[first].current_target(), data["target"])
        self.assertEqual(self.panel.manager.sessions[second].connection, other)
        start.assert_not_called()

    def test_active_session_blocks_full_discovery_without_requesting_stop(self):
        sid = self.fixture.add(42)
        self.panel.manager.start(sid, self.snapshot(), "check")
        session = self.panel.manager.sessions[sid]
        self.assertFalse(self.panel.auto_button.isEnabled())
        with patch("app.multiExecutionUI.DiscoveryWorker") as worker:
            self.panel.discover()
        worker.assert_not_called()
        self.assertIn(sid, self.panel.cards)
        self.assertFalse(session.stop_event.is_set())
        self.assertFalse(session.remove_pending)
        session.events.put(("result", {"succeeded": True, "message": "checked"}))
        session.process.alive = False
        self.panel.manager.poll()
        self.assertTrue(self.panel.auto_button.isEnabled())

    def test_result_arriving_after_session_start_preserves_active_list(self):
        sid = self.fixture.add(42)
        self.panel.manager.start(sid, self.snapshot(), "check")
        session = self.panel.manager.sessions[sid]
        start = self.apply_global_results([self.fixture.adb_connection()])
        self.assertEqual(list(self.panel.manager.sessions), [sid])
        self.assertIn(sid, self.panel.cards)
        self.assertFalse(session.stop_event.is_set())
        self.assertFalse(session.remove_pending)
        start.assert_not_called()

    def test_saved_instance_list_contains_only_the_replacement_results(self):
        self.fixture.add(42)
        found = [self.fixture.adb_connection()]
        self.apply_global_results(found)
        self.window.save_user_config()
        path = self.window.runtime.user_dir / "config" / "user_config.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([record["connection"] for record in saved["multi_instances"]], found)

    def test_real_worker_delivers_repeated_replacements_and_retains_single_target_choices(self):
        self.fixture.add(42)
        found = [self.fixture.adb_connection(), self.fixture.adb_connection("127.0.0.1:16384")]
        with patch("app.multiExecutionUI.discover_connections") as discover, \
                patch("maa.toolkit.Toolkit.find_desktop_windows", return_value=[]), \
                patch.object(self.panel, "start_sessions") as start:
            for targets in (found, found[:1]):
                old_ids = set(self.panel.manager.sessions)
                discover.return_value = (targets, [])
                self.panel.auto_button.click()
                self.assertIsNotNone(self.panel.discovery_worker)
                for _ in range(100):
                    if self.panel.discovery_worker is None:
                        break
                    QTest.qWait(10)
                self.assertIsNone(self.panel.discovery_worker)
                self.assert_targets(targets)
                self.assertTrue(old_ids.isdisjoint(self.panel.manager.sessions))
            self.assertEqual(start.call_count, 2)


if __name__ == "__main__":
    unittest.main()
