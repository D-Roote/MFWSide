# Explicit UI/native data only: never collect project assets or user settings.
from importlib.metadata import distribution
import os
from pathlib import Path
import runpy

from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
)


project = Path(SPECPATH).parent
app_version = runpy.run_path(str(project / 'tools' / 'build_windows.py'))['read_version']()
windows_version = tuple(int(part) for part in app_version.split('.')) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=windows_version, prodvers=windows_version),
    kids=[
        StringFileInfo([StringTable('040904B0', [
            StringStruct('FileDescription', 'MFWSide'),
            StringStruct('FileVersion', app_version),
            StringStruct('InternalName', 'MFWSide'),
            StringStruct('OriginalFilename', 'MFWSide.exe'),
            StringStruct('ProductName', 'MFWSide'),
            StringStruct('ProductVersion', app_version),
        ])]),
        VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),
    ],
)
maa_data, maa_binaries, maa_imports = collect_all('maa', include_py_files=False)
agent_root = Path(distribution('MaaAgentBinary').locate_file('MaaAgentBinary'))

# Android .so files are data for the device, not Windows PE dependencies.
agent_data = [
    (str(path), str(Path('MaaAgentBinary') / path.parent.relative_to(agent_root)))
    for path in agent_root.rglob('*') if path.is_file()
]
ui_data = []
for directory in ('pySide6', 'resources'):
    for path in (project / 'app' / directory).rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            ui_data.append((str(path), str(path.parent.relative_to(project))))

a = Analysis(
    [str(project / 'main.py')],
    pathex=[str(project)],
    binaries=maa_binaries,
    datas=maa_data + agent_data + ui_data,
    hiddenimports=maa_imports + ['PySide6.QtSvg'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2', 'cv2'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='MFWSide',
    version=version_info,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=os.environ.get('MFWSIDE_BUILD_CONSOLE') == '1',
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='MFWSide')
