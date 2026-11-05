import sys
from pathlib import Path
import threading
import unittest
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.sessionIPC import SessionEventSender


class SenderTests(unittest.TestCase):
    def test_blocked_send_does_not_block_callbacks_or_lose_terminal_result(self):
        release, entered = threading.Event(), threading.Event()
        received = []
        def slow(value):
            entered.set()
            release.wait(3)
            received.append(value)
        sender = SessionEventSender(MagicMock(send=slow))
        try:
            sender.put(("log", "first"))
            self.assertTrue(entered.wait(1))
            for n in range(1000):
                sender.put(("log", n))
            sender.put(("disconnected", "offline"))
            sender.put(("result", "done"))
            self.assertLessEqual(len(sender._pending), 512)
            with self.assertRaises(TimeoutError):
                sender.flush(timeout=0.01)
            release.set()
            sender.flush()
            self.assertIn(("result", "done"), received)
            self.assertIn(("disconnected", "offline"), received)
        finally:
            release.set()
            sender.close()
            sender._thread.join(3)

    def test_send_failure_is_visible_to_flush_and_future_put(self):
        sender = SessionEventSender(MagicMock(send=MagicMock(side_effect=OSError("broken"))))
        sender.put(("result", "done"))
        with self.assertRaises(OSError):
            sender.flush()
        with self.assertRaises(OSError):
            sender.put(("log", "next"))
        sender.close()
        sender._thread.join(1)
