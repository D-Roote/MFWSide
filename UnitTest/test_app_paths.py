"""Source/frozen path separation without launching apps, devices or SDK jobs."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.paths import ApplicationPaths, PATHS, resolve_application_paths
from app.runtime import AppRuntime


ROOT = Path(__file__).resolve().parents[1]
INTERFACE = {"controller": [{"name": "Win", "type": "Win32", "win32": {"window_regex": "Example"}}],
             "resource": [{"name": "Bundle", "path": ["."]}], "task": []}


class ApplicationPathTests(unittest.TestCase):
    def test_source_layout_keeps_existing_resource_model_and_user_paths(self):
        self.assertEqual(PATHS.root_dir, ROOT)
        self.assertEqual(PATHS.app_dir, ROOT / "app")
        self.assertEqual(PATHS.resource_dir, ROOT / "assets/resources")
        self.assertEqual(PATHS.resource_dir / "model", ROOT / "assets/resources/model")
        self.assertEqual(PATHS.user_dir, ROOT / "assets/user")
        self.assertEqual(PATHS.interface_path, ROOT / "assets/resources/interface.json")

    def test_ui_and_icons_move_with_package_not_with_project_resources(self):
        self.assertTrue((PATHS.ui_dir / "baseUI.ui").is_file())
        self.assertTrue((PATHS.ui_dir / "style.qss").is_file())
        self.assertTrue((PATHS.ui_resource_dir / "icons/actions/settings.svg").is_file())
        self.assertNotEqual(PATHS.resource_dir, PATHS.ui_resource_dir)

    def test_other_cwd_does_not_change_source_paths_or_create_assets(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                self.assertEqual(resolve_application_paths(frozen=False), PATHS)
                self.assertFalse((Path(directory) / "assets").exists())
            finally:
                os.chdir(original)

    def test_frozen_external_data_is_beside_exe_not_inside_bundle_or_venv(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for bundled in (folder / "install/_internal/app", folder / "temporary_bundle/app"):
                paths = resolve_application_paths(bundled, frozen=True, executable=folder / "install/MAABA.exe")
                self.assertEqual(paths.root_dir, folder / "install")
                self.assertEqual(paths.user_dir, folder / "install/assets/user")
                self.assertEqual(paths.resource_dir, folder / "install/assets/resources")
                self.assertEqual(paths.ui_dir, bundled / "pySide6")
                self.assertEqual(paths.ui_resource_dir, bundled / "resources")

    def test_python_interpreter_directory_does_not_become_source_data_root(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            paths = resolve_application_paths(folder / "project/app", frozen=False,
                                              executable=folder / "venv/Scripts/python.exe")
            self.assertEqual(paths.root_dir, folder / "project")

    def test_runtime_uses_shared_paths_without_creating_native_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = ApplicationPaths(Path(directory), ROOT / "app")
            with patch("app.runtime.PATHS", paths), \
                    patch.object(AppRuntime, "_load_interface", return_value=INTERFACE), \
                    patch("app.runtime.create_user32", return_value=MagicMock()), \
                    patch("app.runtime.Resource") as resource:
                runtime = AppRuntime()
            self.assertEqual(runtime.base_dir, paths.assets_dir)
            self.assertEqual(runtime.interface_path, paths.interface_path)
            self.assertEqual(runtime.user_dir, paths.user_dir)
            resource.assert_not_called()

    def test_resource_bundle_paths_remain_relative_to_interface_not_code_or_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = ApplicationPaths(Path(directory), ROOT / "app")
            with patch("app.runtime.PATHS", paths), \
                    patch.object(AppRuntime, "_load_interface", return_value=INTERFACE), \
                    patch("app.runtime.create_user32", return_value=MagicMock()):
                runtime = AppRuntime()
            runtime.interface_path = paths.resource_dir / "nested/interface.json"
            runtime.resource_config = {"path": ["../base", "overlays/selected"]}
            with patch("app.runtime.Resource") as resource:
                resource.return_value.loaded = True
                resource.return_value.post_bundle.return_value.wait.return_value.succeeded = True
                self.assertTrue(runtime._load_resource()[0])
                self.assertEqual(resource.return_value.post_bundle.call_args_list,
                                 [call(str((runtime.interface_path.parent / path).resolve()))
                                  for path in runtime.resource_config["path"]])

    def test_entry_import_does_not_create_gui_or_run_tasks(self):
        with patch("PySide6.QtWidgets.QApplication") as application, \
                patch("app.winUI.MainWindow") as window, \
                patch("app.crashDiagnostics.enable_fault_log") as fault_log:
            entry = importlib.import_module("main")
            importlib.reload(entry)
        application.assert_not_called()
        window.assert_not_called()
        fault_log.assert_not_called()

    def test_entry_uses_existing_user_debug_path_and_remains_cwd_independent(self):
        entry = importlib.import_module("main")
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch("PySide6.QtWidgets.QApplication") as application, \
                        patch("app.winUI.MainWindow") as window, \
                        patch("app.crashDiagnostics.enable_fault_log") as fault_log:
                    application.return_value.exec.return_value = 7
                    self.assertEqual(entry.main(), 7)
                    fault_log.assert_called_once_with(PATHS.user_dir / "debug")
                    window.return_value.show.assert_called_once()
                self.assertFalse((Path(directory) / "assets").exists())
            finally:
                os.chdir(original)


if __name__ == "__main__":
    unittest.main()
