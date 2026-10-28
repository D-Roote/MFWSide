'''Verify an actual frozen EXE in an isolated copy without tasks or power actions.'''
import argparse
import ctypes
from ctypes import wintypes
import json
import multiprocessing as mp
import os
from pathlib import Path
from queue import Empty
import shutil
import subprocess
import sys
import tempfile
import time

if sys.platform == 'win32':
    # Import before the harness temporarily adopts a frozen executable identity.
    from multiprocessing import popen_spawn_win32


def clean_environment(source=None):
    environment = dict(os.environ if source is None else source)
    for key in list(environment):
        if key.startswith(('PYTHON', 'QT_', 'MAAFW_', '_PYI_', 'PYINSTALLER_')) or key in (
                'VIRTUAL_ENV', 'CONDA_PREFIX', '__PYVENV_LAUNCHER__'):
            environment.pop(key, None)
    system = environment.get('SystemRoot', 'C:/Windows')
    environment['PATH'] = str(Path(system) / 'System32') + os.pathsep + system
    environment['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return environment


def inspect_bundle(directory):
    directory = Path(directory).resolve()
    required = [
        'MFWSide.exe', '_internal/python310.dll',
        '_internal/maa/bin/MaaFramework.dll', '_internal/maa/bin/MaaToolkit.dll',
        '_internal/maa/bin/MaaAgentClient.dll', '_internal/maa/bin/MaaWin32ControlUnit.dll',
        '_internal/maa/bin/MaaAdbControlUnit.dll', '_internal/maa/bin/DirectML.dll',
        '_internal/maa/bin/onnxruntime_maa.dll', '_internal/maa/bin/fastdeploy_ppocr_maa.dll',
        '_internal/maa/bin/opencv_world4_maa.dll',
        '_internal/app/pySide6/baseUI.ui', '_internal/app/pySide6/settings.qss',
        '_internal/app/pySide6/style.qss', '_internal/app/pySide6/dark.qss',
        '_internal/PySide6/plugins/platforms/qwindows.dll',
        '_internal/PySide6/plugins/imageformats/qsvg.dll',
    ]
    missing = [path for path in required if not (directory / path).is_file()]
    if missing:
        raise RuntimeError(f'Missing bundle files: {missing}')
    if (directory / 'assets').exists():
        raise RuntimeError('External resources or user data must not be in a release bundle')
    agents = directory / '_internal' / 'MaaAgentBinary'
    for name in ('maatouch', 'minicap', 'minitouch'):
        if not any(path.is_file() for path in (agents / name).rglob('*')):
            raise RuntimeError(f'Missing device helper: {name}')
    if not list((agents / 'minicap').rglob('*.so')):
        raise RuntimeError('Android minicap .so files were dropped during collection')
    if not list((directory / '_internal/app/resources/icons').rglob('*.svg')):
        raise RuntimeError('Bundled UI icons are missing')
    return {'required_files': len(required), 'adb_device_helpers': True, 'external_assets': False}


def dll_version(directory):
    binary_dir = Path(directory).resolve() / '_internal' / 'maa' / 'bin'
    with os.add_dll_directory(str(binary_dir)):
        framework = ctypes.WinDLL(str(binary_dir / 'MaaFramework.dll'))
        toolkit = ctypes.WinDLL(str(binary_dir / 'MaaToolkit.dll'))
        agent = ctypes.WinDLL(str(binary_dir / 'MaaAgentClient.dll'))
        framework.MaaVersion.argtypes = []
        framework.MaaVersion.restype = ctypes.c_char_p
        return framework.MaaVersion().decode('utf-8')


def windows_for_process(pid):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    result = []

    @callback_type
    def collect(hwnd, _parameter):
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == pid:
            title = ctypes.create_unicode_buffer(1024)
            user32.GetWindowTextW(hwnd, title, len(title))
            result.append((hwnd, title.value))
        return True

    user32.EnumWindows(collect, 0)
    return result


def verify_first_install_defaults(config):
    expected = {
        'minimize_enabled': False, 'minimize_app_on_task_start': False,
        'program_launch_task_enabled': False, 'allow_option_edits_while_running': True,
        'clear_log_on_start': True, 'auto_cleanup_on_exit': True,
    }
    if any(config.get('general', {}).get(key) is not value for key, value in expected.items()):
        raise RuntimeError('The frozen app has unexpected first-install setting defaults')
    if any(value for value in config.get('startup', {}).values() if isinstance(value, bool)):
        raise RuntimeError('Startup actions must default to disabled')
    return True


def verify_gui(directory):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    # Deliberately use another working directory and no Python on PATH.
    process = subprocess.Popen(
        [str(directory / 'MFWSide.exe')], cwd=directory.parent,
        env=clean_environment(), startupinfo=startup,
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        main_window = None
        while time.monotonic() < deadline and process.poll() is None:
            windows = windows_for_process(process.pid)
            for hwnd, title in windows:
                user32.ShowWindow(hwnd, 0)
                if title == 'MFWSide':
                    main_window = hwnd
            if main_window:
                break
            time.sleep(0.05)
        if main_window is None:
            raise RuntimeError(f'Frozen GUI did not initialize: exit={process.poll()}')
        config_path = directory / 'assets/user/config/maa_config.json'
        if not config_path.is_file():
            raise RuntimeError('User data was not created beside the EXE')
        config = json.loads(config_path.read_text(encoding='utf-8'))
        verify_first_install_defaults(config)
        if not user32.PostMessageW(main_window, 0x0010, 0, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        if process.wait(timeout=15) != 0:
            raise RuntimeError(f'Frozen GUI failed during shutdown: {process.returncode}')
        return {'window_title': 'MFWSide', 'clean_exit': True, 'python_removed_from_path': True,
                'alternate_working_directory': True, 'latest_settings_present': True,
                'first_install_defaults': True}
    finally:
        if process.poll() is None:
            process.terminate()  # Only this freshly spawned empty test app.
            process.wait(timeout=10)


def verify_session_ipc(context):
    project = str(Path(__file__).resolve().parents[1])
    sys.path.insert(0, project)
    processes, inbox = [], None
    try:
        # Pure stdlib application types: never construct controllers or tasks.
        from app.sessionIPC import SessionEventInbox, SharedFlag
        flag = SharedFlag(context)
        writer = context.Process(target=flag.set)
        processes.append(writer)
        writer.start()
        if not flag.wait(20):
            raise RuntimeError('Frozen session cancellation flag was not shared')
        writer.join(timeout=10)
        if writer.exitcode != 0:
            raise RuntimeError(f'Frozen shared-flag worker failed: {writer.exitcode}')

        inbox = SessionEventInbox(context)
        expected = ('result', {'succeeded': True, 'message': 'frozen-session-pipe-ok'})
        sender = context.Process(target=inbox.sender.put, args=(expected,))
        processes.append(sender)
        sender.start()
        inbox.close_sender()
        inbox.start()
        deadline = time.monotonic() + 20
        while True:
            try:
                received = inbox.get_nowait()
                break
            except Empty:
                if inbox.error or time.monotonic() >= deadline:
                    raise RuntimeError('Frozen session pipe did not deliver its result: ' + inbox.error)
                time.sleep(0.01)
        if received != expected:
            raise RuntimeError('Frozen session pipe returned unexpected data')
        sender.join(timeout=10)
        if sender.exitcode != 0:
            raise RuntimeError(f'Frozen pipe worker failed: {sender.exitcode}')
        return True
    finally:
        for process in processes:
            if process.pid is not None and process.is_alive():
                process.terminate()  # Only the fresh, headless verification worker.
                process.join(timeout=10)
            if process.pid is not None:
                process.close()
        if inbox is not None:
            inbox.close()
        sys.path.remove(project)


def verify_spawn(executable):
    if popen_spawn_win32.WINENV:
        raise RuntimeError('Run the verification harness with the base Python, not the venv redirector')
    context = mp.get_context('spawn')
    queue = context.Queue()
    process = context.Process(target=queue.put, args=('frozen-spawn-ok',))
    old_executable = sys.executable
    old_frozen = getattr(sys, 'frozen', None)
    old_environment = dict(os.environ)
    from multiprocessing.spawn import get_executable
    old_mp_executable = get_executable()
    try:
        os.environ.clear()
        os.environ.update(clean_environment(old_environment))
        sys.executable = str(executable)
        sys.frozen = True
        mp.set_executable(str(executable))
        process.start()
        if queue.get(timeout=20) != 'frozen-spawn-ok':
            raise RuntimeError('Frozen spawn did not return the expected value')
        process.join(timeout=10)
        if process.exitcode != 0:
            raise RuntimeError(f'Frozen spawn failed: {process.exitcode}')
        return {'standard_queue': True, 'session_ipc': verify_session_ipc(context)}
    finally:
        sys.executable = old_executable
        if old_frozen is None:
            del sys.frozen
        else:
            sys.frozen = old_frozen
        mp.set_executable(old_mp_executable)
        os.environ.clear()
        os.environ.update(old_environment)
        if process.pid is not None and process.is_alive():
            process.terminate()
            process.join(timeout=10)
        if process.pid is not None:
            process.close()
        queue.close()
        queue.join_thread()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--dll-only', action='store_true')
    options = parser.parse_args(argv)
    if sys.platform != 'win32':
        parser.error('Windows is required')
    kernel32 = ctypes.WinDLL('kernel32')
    kernel32.SetErrorMode.argtypes = [wintypes.UINT]
    kernel32.SetErrorMode.restype = wintypes.UINT
    previous = kernel32.SetErrorMode(0x0003)
    kernel32.SetErrorMode(previous | 0x0003)
    directory = options.directory.resolve()
    if options.dll_only:
        print(json.dumps({'maa_version': dll_version(directory)}))
        return 0
    result = inspect_bundle(directory)
    native = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(directory), '--dll-only'],
        check=True, capture_output=True, text=True, timeout=30, env=clean_environment(),
    )
    result.update(json.loads(native.stdout))
    cache = Path(__file__).resolve().parents[1] / '.cache'
    cache.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='frozen-smoke-', dir=cache)).resolve()
    if temporary.parent != cache.resolve():
        raise RuntimeError('Unsafe temporary probe path')
    success = False
    try:
        isolated = temporary / 'MFWSide'
        shutil.copytree(directory, isolated)
        result['gui'] = verify_gui(isolated)
        spawn = verify_spawn(isolated / 'MFWSide.exe')
        result['frozen_multiprocessing_spawn'] = spawn['standard_queue']
        result['frozen_session_ipc'] = spawn['session_ipc']
        result['ok'] = True
        success = True
    finally:
        if success and temporary.resolve().parent == cache.resolve():
            shutil.rmtree(temporary)  # Only the verified, newly created probe copy.
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    mp.freeze_support()
    raise SystemExit(main())
