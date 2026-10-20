# Explicit UI/native data only: never collect project assets or user settings.
from importlib.metadata import distribution
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


project = Path(SPECPATH).parent
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
    excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='MFWSide',
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
