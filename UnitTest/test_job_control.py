import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest.mock import MagicMock, patch
from app.jobControl import wait_job


class JobControlTests(unittest.TestCase):
    def test_cleanup_timeout_does_not_enter_native_wait(self):
        job = MagicMock(done=False)
        with self.assertRaises(TimeoutError):
            wait_job(job, timeout=0)
        job.wait.assert_not_called()

    def test_cancellation_starts_grace_not_normal_work_timeout(self):
        job = MagicMock(done=False)
        with patch("app.jobControl.time.monotonic", side_effect=[0, 10000, 10006]), \
                patch("app.jobControl.time.sleep"), self.assertRaises(TimeoutError):
            wait_job(job, cancelled=MagicMock(side_effect=[False, True]))
        job.wait.assert_not_called()

    def test_completed_job_returns_result(self):
        job = MagicMock(done=True)
        self.assertIs(wait_job(job, timeout=0), job.wait.return_value)
