'''Release archives reject external data and verification uses isolated defaults.'''
from pathlib import Path
from copy import deepcopy
import multiprocessing as mp
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import build_windows, verify_windows_build


class WindowsBuildToolingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle = self.root / 'MFWSide'
        self.bundle.mkdir()
        (self.bundle / 'MFWSide.exe').write_bytes(b'fake-exe')

    def test_bundle_files_include_internal_ui_without_external_assets(self):
        icon = self.bundle / '_internal/app/resources/icons/test.svg'
        icon.parent.mkdir(parents=True)
        icon.touch()
        self.assertEqual(len(build_windows.bundle_files(self.bundle)), 2)

    def test_each_non_release_directory_is_rejected(self):
        for name in ('assets', '.venv', '.cache', 'UnitTest', '__pycache__', '.git'):
            with self.subTest(name=name):
                bad = self.bundle / name
                bad.mkdir()
                path = bad / 'do-not-distribute.txt'
                path.touch()
                with self.assertRaises(ValueError):
                    build_windows.bundle_files(self.bundle)
                path.unlink()
                bad.rmdir()

    def test_archive_has_one_application_root_and_expected_hash(self):
        archive = build_windows.archive_bundle(self.bundle, self.root / 'test.zip')
        with zipfile.ZipFile(archive) as content:
            self.assertEqual(content.namelist(), ['MFWSide/MFWSide.exe'])
            self.assertEqual(content.read(content.namelist()[0]), b'fake-exe')
        empty = self.root / 'empty.bin'
        empty.touch()
        self.assertEqual(build_windows.sha256_file(empty),
                         'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855')

    def test_version_is_numeric_major_minor_patch_and_windows_compatible(self):
        path = self.root / 'VERSION'
        for value in ('0.0.0', '1.0.0', '12.34.56', '65535.0.1'):
            path.write_text(value + '\n', encoding='utf-8')
            self.assertEqual(build_windows.read_version(path), value)
        for value in ('1.0', 'v1.0.0', '01.0.0', '1.0.0-rc1', '1.0.0.0',
                      '../1.0.0', '65536.0.0', '-1.0.0', '1.\u0660.0'):
            with self.subTest(value=value):
                path.write_text(value, encoding='utf-8')
                with self.assertRaises(ValueError):
                    build_windows.read_version(path)

    def test_resource_launchers_and_licenses_cannot_enter_an_app_archive(self):
        for name in ('Start-ANReroll.cmd', 'License.Mareroll', 'LICENSE.MAAREROLL'):
            with self.subTest(name=name):
                path = self.bundle / name
                path.touch()
                archive = self.root / 'blocked.zip'
                with self.assertRaisesRegex(ValueError, 'Resource-only file'):
                    build_windows.archive_bundle(self.bundle, archive)
                self.assertFalse(archive.exists())
                path.unlink()

    def test_notice_collection_omits_only_resource_specific_licenses(self):
        for name in ('License.Mareroll', 'License.MaaReroll', 'LICENSE.md'):
            (self.root / name).write_text(name, encoding='utf-8')
        package = SimpleNamespace(
            files=[Path('License.Mareroll'), Path('License.MaaReroll'), Path('LICENSE.md')],
            locate_file=lambda path: self.root / path,
        )
        with patch.object(build_windows, 'distribution', return_value=package):
            build_windows.copy_notices(self.root / 'notices')
        names = [path.name for path in (self.root / 'notices').rglob('*') if path.is_file()]
        self.assertIn('LICENSE.md', names)
        self.assertIn('Python-LICENSE.txt', names)
        self.assertFalse(any(name.casefold() in build_windows.RESOURCE_ONLY_FILES for name in names))

    def test_archive_refuses_to_package_user_settings(self):
        settings = self.bundle / 'assets/user/config'
        settings.mkdir(parents=True)
        (settings / 'user_config.json').write_text('{}', encoding='utf-8')
        destination = self.root / 'unsafe.zip'
        with self.assertRaises(ValueError):
            build_windows.archive_bundle(self.bundle, destination)
        self.assertFalse(destination.exists())

    def test_clean_environment_drops_external_python_qt_and_sdk_overrides(self):
        original = {'SystemRoot': 'C:/Windows', 'PATH': 'C:/Python',
                    'PYTHONPATH': 'private', 'QT_PLUGIN_PATH': 'external',
                    'MAAFW_BINARY_PATH': 'external', 'VIRTUAL_ENV': 'private',
                    '_PYI_APPLICATION_HOME_DIR': 'old', 'KEEP_ME': 'yes'}
        result = verify_windows_build.clean_environment(original)
        self.assertEqual(result['KEEP_ME'], 'yes')
        self.assertNotIn('C:/Python', result['PATH'])
        for key in ('PYTHONPATH', 'QT_PLUGIN_PATH', 'MAAFW_BINARY_PATH',
                    'VIRTUAL_ENV', '_PYI_APPLICATION_HOME_DIR'):
            self.assertNotIn(key, result)
        self.assertEqual(original['PYTHONPATH'], 'private')
        self.assertEqual(result['PYINSTALLER_RESET_ENVIRONMENT'], '1')

    def test_native_manifest_refuses_an_incomplete_bundle(self):
        with self.assertRaisesRegex(RuntimeError, 'Missing bundle files'):
            verify_windows_build.inspect_bundle(self.bundle)

    def test_first_install_probe_accepts_only_the_requested_checked_defaults(self):
        config = {'general': {
            'minimize_enabled': False, 'minimize_app_on_task_start': False,
            'program_launch_task_enabled': False, 'allow_option_edits_while_running': True,
            'clear_log_on_start': True, 'auto_cleanup_on_exit': True,
        }, 'startup': {'auto_start_tasks': False, 'task_wait_seconds': 0}}
        self.assertTrue(verify_windows_build.verify_first_install_defaults(config))
        for key in config['general']:
            changed = deepcopy(config)
            changed['general'][key] = not config['general'][key]
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                verify_windows_build.verify_first_install_defaults(changed)
        config['startup']['auto_start_tasks'] = True
        with self.assertRaises(RuntimeError):
            verify_windows_build.verify_first_install_defaults(config)

    def test_session_probe_uses_spawn_and_real_pipe_without_native_controllers(self):
        self.assertTrue(verify_windows_build.verify_session_ipc(mp.get_context('spawn')))

    def test_build_guard_preserves_existing_output_user_data(self):
        assets = self.root / 'dist/MFWSide/assets/user'
        assets.mkdir(parents=True)
        sentinel = assets / 'settings.json'
        sentinel.write_text('keep', encoding='utf-8')
        with patch.object(build_windows, 'ROOT', self.root), \
                patch.object(build_windows.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'external assets/user data'):
                build_windows.main(['--skip-tests'])
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep')
        run.assert_not_called()

    def test_notice_collection_stays_inside_its_destination(self):
        destination = self.root / 'licenses'
        build_windows.copy_notices(destination)
        self.assertTrue((destination / 'Python-LICENSE.txt').is_file())
        self.assertTrue(list((destination / 'MaaFw').rglob('LICENSE.md')))
        self.assertTrue(list((destination / 'PySide6_Essentials').rglob('LICENSE*')))
        self.assertNotIn('opencv-python', build_windows.RUNTIME_PACKAGES)
        self.assertFalse((destination / 'opencv-python').exists())
        self.assertTrue(all(path.resolve().is_relative_to(destination.resolve())
                            for path in destination.rglob('*')))
