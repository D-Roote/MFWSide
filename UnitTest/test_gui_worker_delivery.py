"""Actual QThread result delivery, with discovery/controller operations fully mocked."""
from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from unittest.mock import patch
import unittest

import test_runtime_lifecycle as fixtures


class GuiWorkerDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window

    def wait_for(self, callbacks, worker):
        for _ in range(100):
            if callbacks and worker.isFinished():
                return
            QTest.qWait(10)
        self.fail("Worker did not deliver its result")

    def test_discovery_completion_runs_on_gui_thread(self):
        panel = self.window.multi_panel
        callbacks = []
        with patch("app.multiExecutionUI.discover_connections", return_value=([], [])), \
                patch.object(panel, "_discovered", side_effect=lambda *args: callbacks.append(QThread.currentThread())):
            panel.discover()
            worker = panel.discovery_worker
            try:
                self.wait_for(callbacks, worker)
                self.assertIs(callbacks[0], self.fixture.app.thread())
            finally:
                worker.wait(1000)
                panel.discovery_worker = None
                worker.deleteLater()

    def test_monitor_completion_runs_on_gui_thread(self):
        monitor = self.window.monitor
        callbacks = []
        with patch.object(monitor, "_finished", side_effect=lambda *args: callbacks.append(QThread.currentThread())):
            monitor._request("connect", lambda: "mocked result")
            worker = monitor.worker
            try:
                self.wait_for(callbacks, worker)
                self.assertIs(callbacks[0], self.fixture.app.thread())
            finally:
                worker.wait(1000)
                monitor.worker = None
                worker.deleteLater()
