"""Project-neutral guidance without changing task options or connecting devices."""
import unittest
from unittest.mock import patch

import test_runtime_lifecycle as fixtures
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget


class UIGuidanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.window.show()
        QApplication.processEvents()

    def test_detail_pane_contains_only_the_intact_settings_scroll_area(self):
        self.assertIsNone(self.window.findChild(QLabel, "infoLabel"))
        layout = self.window.findChild(QVBoxLayout, "settingWidget_1")
        self.assertEqual(layout.count(), 1)
        scroll = layout.itemAt(0).widget()
        self.assertEqual(scroll.objectName(), "scrollSettingWidget")
        self.assertEqual(scroll.widget().objectName(), "scrollSettingContents")
        self.assertTrue(scroll.widgetResizable())
        self.assertGreater(scroll.viewport().height(), 0)

    def test_extra_feature_overview_invites_github_issue_suggestions(self):
        overview = self.window.extra_features_panel.findChild(QWidget, "extraFeatureOverview")
        labels = overview.findChildren(QLabel, "settingsSectionDescription")
        self.assertEqual([label.text() for label in labels],
                         ["추가 기능에 대한 건의는 GitHub Issue로 남겨주세요."])

    def test_empty_win32_discovery_explains_only_program_execution(self):
        self._assert_empty_discovery("대상이 없습니다. 프로그램 실행 상태를 확인하세요.")

    def test_empty_adb_discovery_explains_only_adb_connection(self):
        monitor = self.window.monitor
        preset = {"name": "Adb", "type": "Adb", "label": "ADB"}
        monitor.service.presets.append(preset)
        self.window.settings_panel.controllers.append(preset)
        self.window.settings_panel.controller_combo.addItem("ADB", preset)
        monitor.select_preset("Adb")
        self._assert_empty_discovery("대상이 없습니다. ADB 연결 상태를 확인하세요.")

    def _assert_empty_discovery(self, message):
        monitor = self.window.monitor
        with patch.object(monitor.service, "discover", return_value=[]):
            monitor.discover()
            self.fixture.wait_for_monitor()
        self.assertEqual(monitor.targets, [])
        self.assertEqual(monitor.target_key, "")
        self.assertEqual(monitor.status, message)
        for panel in monitor.panels:
            self.assertEqual(panel.status.text(), message)


if __name__ == "__main__":
    unittest.main()
