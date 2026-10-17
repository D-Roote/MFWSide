"""Only temporary files are removed; live project/session data is never changed."""
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import test_runtime_lifecycle as fixtures
from app.storageManagement import (
    cleanup_storage, format_size, reset_settings_files, scan_storage, storage_groups,
)
from app.settingsUI import DEFAULT_MAA_CONFIG


class StorageManagementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.user = self.root / "assets/user"
        self.user.mkdir(parents=True)
        self.groups = storage_groups(self.user, self.root, self.root / "app", self.root / "assets/resources")

    def put(self, name, data=b"1234567890"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def scan(self):
        return scan_storage(self.groups, self.user)

    def test_debug_images_logs_backups_and_cache_are_counted(self):
        for name in ("assets/user/debug/maafw.log", "assets/user/debug/maafw.bak.old.log",
                     "assets/user/debug/error/shot.png", "app/__pycache__/main.cpython-310.pyc",
                     ".cache/test_diagnostics/python_fault_1.log", ".cache/suite.log"):
            self.put(name)
        usage = {entry.group.key: entry for entry in self.scan()}
        self.assertEqual(usage["debug"].bytes, 30)
        self.assertEqual(usage["debug"].removable_bytes, 20)
        self.assertEqual(usage["app_cache"].bytes, 10)
        self.assertEqual(usage["test_logs"].bytes, 10)
        self.assertEqual(usage["dev_logs"].bytes, 10)

    def test_resource_model_accounts_config_and_source_are_not_removed(self):
        protected = [self.put(name) for name in ("assets/resources/image/test.png", "assets/resources/model/ocr.bin",
                     "assets/saved/account.json", "assets/user/config/user_config.json", "app/main.py",
                     ".cache/MaaReroll/debug/example.log", ".git/test.log", ".venv/test.log")]
        log = self.put("assets/user/sessions/instance/run/debug/maafw.log")
        config = self.put("assets/user/sessions/instance/run/config/maa_option.json")
        report = cleanup_storage(self.scan(), self.user)
        self.assertEqual(report.files, 1)
        self.assertFalse(log.exists())
        self.assertTrue(config.exists())
        self.assertTrue(all(path.exists() for path in protected))

    def test_active_run_and_discovery_are_preserved(self):
        live = self.put(f"assets/user/sessions/live/run/python_fault_{os.getpid()}.log")
        log = self.put("assets/user/sessions/live/run/debug/maafw.log")
        discovery = self.put("assets/user/sessions/discovery/debug/maafw.log")
        report = cleanup_storage(self.scan(), self.user)
        self.assertEqual(report.files, 0)
        self.assertTrue(live.exists() and log.exists() and discovery.exists())

    def test_inactive_pid_run_can_be_cleaned(self):
        marker = self.put("assets/user/sessions/old/run/python_fault_12345.log")
        self.put("assets/user/sessions/old/run/debug/maafw.log")
        with patch("app.storageManagement.process_active", return_value=False):
            report = cleanup_storage(self.scan(), self.user)
        self.assertEqual(report.files, 2)
        self.assertFalse(marker.exists())

    def test_log_updated_after_scan_is_not_deleted(self):
        path = self.put("assets/user/debug/old.log")
        usages = self.scan()
        future = time.time() + 3
        os.utime(path, (future, future))
        result = cleanup_storage(usages, self.user)
        self.assertTrue(path.exists())
        self.assertEqual(result.skipped, 1)

    def test_symlink_is_not_followed_or_deleted(self):
        target = self.put("outside/keep.log")
        link = self.user / "debug"
        try:
            link.symlink_to(target.parent, target_is_directory=True)
        except OSError:
            self.skipTest("Windows symlink creation is not permitted")
        report = cleanup_storage(self.scan(), self.user)
        self.assertEqual(report.files, 0)
        self.assertTrue(target.exists())
        self.assertTrue(link.is_symlink())

    def test_reparse_directory_is_skipped_without_following(self):
        path = self.put("assets/user/debug/junction/keep.log")
        original = Path.lstat
        def lstat(candidate):
            if candidate == path.parent:
                return SimpleNamespace(st_mode=0o040755, st_file_attributes=0x400)
            return original(candidate)
        with patch.object(Path, "lstat", lstat):
            result = cleanup_storage(self.scan(), self.user)
        self.assertEqual(result.files, 0)
        self.assertTrue(path.exists())

    def test_reset_rolls_back_completed_writes_on_failure(self):
        from app.settingsUI import SettingsStore
        path = self.put("assets/user/config/maa_config.json", b'{"custom": true}')
        original = SettingsStore._write_json
        def write(target, value):
            if target.name == "user_config.json":
                raise OSError("full disk")
            original(target, value)
        with patch.object(SettingsStore, "_write_json", side_effect=write), self.assertRaises(OSError):
            reset_settings_files(self.user, DEFAULT_MAA_CONFIG)
        self.assertEqual(path.read_bytes(), b'{"custom": true}')

    def test_missing_folders_and_cancel_are_safe(self):
        self.assertEqual(sum(usage.bytes for usage in self.scan()), 0)
        self.assertEqual(scan_storage(self.groups, self.user, lambda: True), [])
        self.assertEqual(cleanup_storage(self.scan(), self.user, lambda: True).files, 0)
        self.assertEqual(format_size(1024 ** 3), "1.0 GiB")

    def test_reset_only_application_config_files(self):
        saved = self.put("assets/saved/account.json")
        resource = self.put("assets/resources/interface.json")
        self.put("assets/user/config/user_config.json", b'{"tasks": ["custom"]}')
        reset_settings_files(self.user, DEFAULT_MAA_CONFIG)
        config = self.user / "config"
        self.assertEqual(json.loads((config / "user_config.json").read_text()), {})
        self.assertEqual(json.loads((config / "maa_config.json").read_text()), DEFAULT_MAA_CONFIG)
        self.assertTrue(saved.exists() and resource.exists())

    def test_permission_failure_is_reported_not_silenced(self):
        path = self.put("assets/user/debug/old.log")
        usages = self.scan()
        with patch.object(Path, "unlink", side_effect=PermissionError("locked")):
            report = cleanup_storage(usages, self.user)
        self.assertEqual(report.files, 0)
        self.assertTrue(report.errors)
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
