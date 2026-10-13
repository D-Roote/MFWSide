"""Read-only discovery and mocked launch tests; never start Steam or a game."""
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import test_runtime_lifecycle as fixtures
from app import pg_init
from app.runtime import AppRuntime


class GenericDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.steam = self.root / "Registered Client"
        self.steam.mkdir()
        (self.steam / "steam.exe").touch()
        self.controller = {"name": "Window", "type": "Win32",
                           "win32": {"window_regex": "^Example Game$", "class_regex": "^GameClass$"}}
        self.config = {"controller": self.controller}
        def mock_target(target, **kwargs):
            patcher = patch(target, **kwargs)
            result = patcher.start()
            self.addCleanup(patcher.stop)
            return result
        self.windows = mock_target("maa.toolkit.Toolkit.find_desktop_windows", return_value=[])
        self.process = mock_target("app.monitoring.window_process_info")
        self.registry = mock_target("app.pg_init.registered_steam_roots", return_value=[self.steam])

    def install(self, app_id="123", name="Example Game", directory="Unrelated Folder", library=None):
        library = library or self.steam
        installed = library / "steamapps/common" / directory
        installed.mkdir(parents=True, exist_ok=True)
        manifest = library / "steamapps" / f"appmanifest_{app_id}.acf"
        manifest.write_text('"AppState"\n{\n' +
                            f'"appid" "{app_id}"\n"name" "{name}"\n"installdir" "{directory}"\n' +
                            '}\n', encoding="utf-8")
        return installed

    def test_defaults_and_old_config_do_not_name_any_game(self):
        result = pg_init.normalize_program_config({"executable_name": "Old.exe", "search_paths": ["Old/Old.exe"]})
        self.assertNotIn("executable_name", result)
        self.assertNotIn("search_paths", result)
        self.assertEqual(result["startup_wait_seconds"], 60)

    def test_running_window_uses_actual_exe_and_class_filter(self):
        executable = self.root / "Completely Different.exe"
        executable.touch()
        self.windows.return_value = [SimpleNamespace(hwnd=10, window_name="Example Game", class_name="Other"),
                                     SimpleNamespace(hwnd=11, window_name="Example Game", class_name="GameClass")]
        self.process.return_value = (42, str(executable))
        self.assertEqual(pg_init.find_auto_program_executable(self.config), executable.resolve())
        self.process.assert_called_once_with(11)
        self.registry.assert_not_called()

    def test_class_only_can_locate_running_program(self):
        self.controller["win32"]["window_regex"] = ""
        executable = self.root / "Process.exe"
        executable.touch()
        self.windows.return_value = [SimpleNamespace(hwnd=10, window_name="Anything", class_name="GameClass")]
        self.process.return_value = (42, str(executable))
        self.assertEqual(pg_init.find_auto_program_executable(self.config), executable.resolve())

    def test_distinct_running_programs_are_ambiguous(self):
        paths = [self.root / "First.exe", self.root / "Second.exe"]
        for path in paths:
            path.touch()
        self.windows.return_value = [SimpleNamespace(hwnd=i, window_name="Example Game", class_name="GameClass")
                                     for i in (10, 11)]
        self.process.side_effect = [(42, str(paths[0])), (43, str(paths[1]))]
        self.install()
        self.assertIsNone(pg_init.find_auto_program_executable(self.config))

    def test_unreadable_running_process_does_not_select_another_steam_installation(self):
        self.windows.return_value = [SimpleNamespace(hwnd=10, window_name="Example Game", class_name="GameClass")]
        self.process.return_value = (42, "")
        self.install()
        self.assertIsNone(pg_init.find_auto_program_executable(self.config))

    def test_closed_steam_game_uses_install_root_without_guessing_an_exe(self):
        installed = self.install()
        for name in ("Game.exe", "Launcher.exe", "CrashReporter.exe"):
            (installed / name).touch()
        self.assertEqual(pg_init.find_auto_program_executable(self.config), installed.resolve())
        self.assertEqual(pg_init.build_program_launch_command(installed),
                         ([str(self.steam / "steam.exe"), "steam://run/123"], self.steam))

    def test_modern_secondary_library_and_missing_launcherpath(self):
        library = self.root / "User Chosen Library"
        installed = self.install(library=library)
        folders = self.steam / "steamapps/libraryfolders.vdf"
        folders.parent.mkdir(exist_ok=True)
        folders.write_text('"libraryfolders" { "0" { "path" ' + json.dumps(str(library)) +
                           ' "apps" { "123" "0" } } }', encoding="utf-8")
        self.assertEqual(pg_init.find_auto_program_executable(self.config), installed.resolve())
        self.assertEqual(pg_init.find_steam_launch(installed)[1], "steam://run/123")

    def test_legacy_libraryfolders_and_duplicate_entry(self):
        library = self.root / "Legacy Library"
        installed = self.install(library=library)
        folders = self.steam / "steamapps/libraryfolders.vdf"
        folders.parent.mkdir(exist_ok=True)
        folders.write_text('"LibraryFolders" { "1" ' + json.dumps(str(library)) +
                           ' "2" ' + json.dumps(str(library)) + ' }', encoding="utf-8")
        self.assertEqual(pg_init.find_auto_program_executable(self.config), installed.resolve())

    def test_registry_uses_actual_registered_client_and_ignores_missing_values(self):
        self.registry.stop()  # Exercise the registry reader itself without querying the real registry.
        key = MagicMock()
        key.__enter__.return_value = key
        with patch("winreg.OpenKey", side_effect=[key, OSError(), OSError()]), \
                patch("winreg.QueryValueEx", return_value=(str(self.steam), 1)):
            self.assertEqual(pg_init.registered_steam_roots(), [self.steam.resolve()])

    def test_invalid_manifest_path_and_zero_appid_are_ignored(self):
        self.install()
        manifest = self.steam / "steamapps/appmanifest_123.acf"
        for app_id, directory in (("0", "Unrelated Folder"), ("123", "bad\0path")):
            manifest.write_text(f'"AppState" {{ "appid" "{app_id}" "name" "Example Game" '
                                f'"installdir" "{directory}" }}', encoding="utf-8")
            self.assertEqual(pg_init.steam_installations(), [])

    def test_missing_steam_client_does_not_produce_a_launch_target(self):
        self.install()
        (self.steam / "steam.exe").unlink()
        self.assertIsNone(pg_init.find_auto_program_executable(self.config))

    def test_same_name_multiple_installs_and_fuzzy_title_require_manual(self):
        self.install()
        self.install("456", directory="Other Folder")
        self.assertIsNone(pg_init.find_auto_program_executable(self.config))
        for title in (".*", ".+", "^Example.*$", "^(Example|Other) Game$", "["):
            self.controller["win32"]["window_regex"] = title
            self.assertIsNone(pg_init.find_auto_program_executable(self.config))

    def test_project_label_does_not_identify_a_steam_game(self):
        self.install()
        self.controller["label"] = "Example Game"
        self.controller["win32"]["window_regex"] = "^Unrelated Window$"
        self.assertIsNone(pg_init.find_auto_program_executable(self.config))

    def test_invalid_adb_or_unconfigured_controller_never_searches(self):
        for controller in (None, {"type": "Adb"}, {"type": "Win32", "win32": {}},
                           {"type": "Win32", "win32": {"window_regex": "["}}):
            self.assertIsNone(pg_init.find_auto_program_executable({"controller": controller}))
        self.windows.assert_not_called()
        self.registry.assert_not_called()

    def test_path_escape_missing_folder_or_invalid_appid_is_rejected(self):
        installed = self.install()
        manifest = self.steam / "steamapps/appmanifest_123.acf"
        for directory, app_id in (("../../outside", "123"), ("missing", "123"), ("Unrelated Folder", "456")):
            manifest.write_text(f'"AppState" {{ "appid" "{app_id}" "name" "Example Game" '
                                f'"installdir" "{directory}" }}', encoding="utf-8")
            self.assertIsNone(pg_init.find_auto_program_executable(self.config))

    def test_keyvalues_comments_escapes_braces_and_malformed_data(self):
        path = self.root / "sample.vdf"
        path.write_text('// header\n"Root" { "name" "Game {edition}" "quote" "a\\\"b" }', encoding="utf-8")
        self.assertEqual(pg_init._read_keyvalues(path), {"root": {"name": "Game {edition}", "quote": 'a"b'}})
        for text in ('"Root" { "name"', '"Root" {', '"Root" "unterminated', '}'):
            path.write_text(text, encoding="utf-8")
            self.assertEqual(pg_init._read_keyvalues(path), {})

    def test_arbitrary_manual_exe_and_arguments_override_auto(self):
        executable = self.root / "Not a game name.exe"
        executable.touch()
        command = f'"{executable}" --profile "two words"'
        with patch("app.pg_init.find_auto_program_executable") as auto:
            self.assertEqual(pg_init.find_program_executable({"manual_path": command}), executable.resolve())
        auto.assert_not_called()
        self.assertEqual(pg_init.build_program_launch_command(executable, ["--profile", "two words"])[0],
                         [str(executable), "--profile", "two words"])

    def test_invalid_or_deleted_manual_path_never_falls_back(self):
        self.install()
        with patch("app.pg_init.find_auto_program_executable") as auto:
            self.assertIsNone(pg_init.find_program_executable({**self.config, "manual_path": "missing.exe"}))
        auto.assert_not_called()

    def test_legacy_manual_folder_only_when_one_exe(self):
        (self.root / "Whatever.exe").touch()
        self.assertEqual(pg_init.resolve_manual_program_path(str(self.root)), (self.root / "Whatever.exe").resolve())
        (self.root / "Updater.exe").touch()
        self.assertIsNone(pg_init.resolve_manual_program_path(str(self.root)))

    def test_explicit_arguments_are_not_lost_to_steam_url_conversion(self):
        installed = self.install()
        executable = installed / "Anything.exe"
        executable.touch()
        self.assertEqual(pg_init.build_program_launch_command(executable, ["--profile", "2"])[0],
                         [str(executable), "--profile", "2"])

    def test_runtime_does_not_treat_running_steam_client_as_running_game(self):
        installed = self.install()
        runtime = AppRuntime()
        with patch.object(runtime, "_find_target_window", return_value=(None, "not running")), \
                patch.object(runtime, "_program_is_running", return_value=True) as process_running, \
                patch("app.runtime.subprocess.Popen") as launch:
            success, message = runtime._launch_program({"resolved_path": str(installed)})
        self.assertTrue(success, message)
        process_running.assert_not_called()
        self.assertEqual(launch.call_args.args[0], [str(self.steam / "steam.exe"), "steam://run/123"])
        self.assertFalse(launch.call_args.kwargs["shell"])

    def test_runtime_steam_install_skips_launch_only_when_target_window_exists(self):
        installed = self.install()
        runtime = AppRuntime()
        with patch.object(runtime, "_find_target_window", return_value=(SimpleNamespace(hwnd=10), "running")), \
                patch("app.runtime.subprocess.Popen") as launch:
            success, _ = runtime._launch_program({"resolved_path": str(installed)})
        self.assertTrue(success)
        self.assertFalse(runtime._program_started_for_session)
        launch.assert_not_called()

    def test_runtime_keeps_selected_controller_for_steam_running_window_check(self):
        installed = self.install()
        runtime = AppRuntime()
        runtime.controller_config = self.controller
        with patch.object(runtime, "_find_target_window", return_value=(None, "missing")) as find_window, \
                patch("app.runtime.subprocess.Popen"):
            self.assertTrue(runtime._launch_program({"resolved_path": str(installed)})[0])
        find_window.assert_called_once_with(self.controller)

    def test_runtime_passes_manual_arguments_without_shell(self):
        executable = self.root / "Whatever.exe"
        executable.touch()
        runtime = AppRuntime()
        with patch.object(runtime, "_program_is_running", return_value=False), \
                patch("app.runtime.subprocess.Popen") as launch:
            self.assertTrue(runtime._launch_program({"resolved_path": str(executable),
                                                     "launch_arguments": ["--profile", "2"]})[0])
        self.assertEqual(launch.call_args.args[0], [str(executable), "--profile", "2"])
        self.assertFalse(launch.call_args.kwargs["shell"])


class GenericSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UILifecycleTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.UILifecycleTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.panel = self.fixture.window.settings_panel

    def test_manual_win32_command_preserves_arguments_and_resets(self):
        executable = self.fixture.window.runtime.user_dir / "Other Program.exe"
        executable.touch()
        self.panel.program_path_input.setText(f'"{executable}" --profile "two words"')
        self.panel._apply_program_path()
        config = self.panel.program_settings()
        self.assertEqual(config["resolved_path"], str(executable.resolve()))
        self.assertEqual(config["launch_arguments"], ["--profile", "two words"])
        self.assertNotIn("executable_name", self.panel.config["program"])
        self.panel._apply_program_path()
        self.assertFalse(self.panel.program_settings()["resolved_path"])

    def test_browse_selects_exe_not_a_directory(self):
        with patch("app.settingsUI.QFileDialog.getOpenFileName", return_value=("C:/Program/Game.exe", "")) as browse:
            self.panel._browse_program_path()
        browse.assert_called_once()
        self.assertEqual(self.panel.program_path_input.text(), '"C:/Program/Game.exe"')

    def test_snapshot_does_not_repeat_native_discovery(self):
        with patch("app.settingsUI.find_auto_program_executable", return_value=None) as auto:
            self.panel._refresh_program_status()
            for _ in range(5):
                self.panel.program_settings()
        auto.assert_called_once()

    def test_failed_manual_confirmation_disables_auto_until_reset(self):
        executable = self.fixture.window.runtime.user_dir / "Auto.exe"
        executable.touch()
        with patch("app.settingsUI.find_auto_program_executable", return_value=executable):
            self.panel._refresh_program_status()
            changed = MagicMock()
            self.panel.program_changed.connect(changed)
            self.panel.program_path_input.setText('"missing.exe" --profile 2')
            self.panel._apply_program_path()
            self.assertFalse(self.panel.program_settings()["resolved_path"])
            self.assertFalse(changed.call_args.args[0]["resolved_path"])
            self.panel._apply_program_path()
            self.assertEqual(self.panel.program_settings()["resolved_path"], str(executable))
