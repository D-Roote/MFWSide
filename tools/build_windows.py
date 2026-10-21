'''Reproducible Windows onedir build; external assets never enter the archive.'''
import argparse
from datetime import datetime, timezone
from importlib.metadata import distribution, version
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import os
import zipfile


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGES = (
    'MaaFw', 'MaaAgentBinary', 'PySide6', 'PySide6_Addons', 'PySide6_Essentials',
    'shiboken6', 'numpy', 'StrEnum', 'tomli',
)
RESOURCE_ONLY_FILES = frozenset({'start-anreroll.cmd', 'license.mareroll', 'license.maareroll'})


def read_version(path=None):
    value = (ROOT / 'VERSION' if path is None else Path(path)).read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise ValueError('VERSION must use the numeric major.minor.patch format')
    if any(int(part) > 65535 for part in value.split('.')):
        raise ValueError('VERSION components must fit the Windows version resource (0..65535)')
    return value


def bundle_files(directory):
    directory = Path(directory)
    files = sorted(path for path in directory.rglob('*') if path.is_file())
    forbidden = {'assets', '.venv', '.cache', 'UnitTest', '__pycache__', '.git'}
    for path in files:
        if forbidden.intersection(path.relative_to(directory).parts):
            raise ValueError(f'Non-release data in bundle: {path.relative_to(directory)}')
        if path.name.casefold() in RESOURCE_ONLY_FILES:
            raise ValueError(f'Resource-only file in app bundle: {path.relative_to(directory)}')
    return files


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def archive_bundle(directory, destination):
    directory, destination = Path(directory), Path(destination)
    files = bundle_files(directory)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, Path(directory.name) / path.relative_to(directory))
    return destination


def copy_notices(destination):
    destination = Path(destination)
    for name in (*RUNTIME_PACKAGES, 'PyInstaller'):
        package = distribution(name)
        for relative in package.files or ():
            if relative.name.casefold() in RESOURCE_ONLY_FILES:
                continue
            if '..' in relative.parts or not relative.name.casefold().startswith(
                    ('license', 'licence', 'copying', 'notice', 'copyright')):
                continue
            source = Path(package.locate_file(relative))
            if source.is_file():
                target = destination / name / Path(str(relative))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    python_notice = Path(sys.base_prefix) / 'LICENSE.txt'
    if not python_notice.is_file():
        raise FileNotFoundError('Python LICENSE.txt is missing')
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(python_notice, destination / 'Python-LICENSE.txt')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-tests', action='store_true', help='Use only after a separate full regression run')
    parser.add_argument('--console', action='store_true', help='Diagnostic console build instead of GUI release')
    args = parser.parse_args(argv)
    if sys.platform != 'win32' or struct.calcsize('P') != 8 or sys.version_info[:2] != (3, 10):
        parser.error('Build with CPython 3.10 x64 on Windows')
    output = ROOT / 'dist' / 'MFWSide'
    if (output / 'assets').exists():
        raise RuntimeError('Output has external assets/user data; move it to a backup before rebuilding')
    app_version = read_version()
    if not args.skip_tests:
        subprocess.run([sys.executable, 'UnitTest/run_tests.py', '-q'], cwd=ROOT, check=True, timeout=1800)
    build_env = dict(os.environ)
    build_env['MFWSIDE_BUILD_CONSOLE'] = '1' if args.console else '0'
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
        '--workpath', str(ROOT / 'build' / 'windows'),
        '--distpath', str(ROOT / 'dist'), str(ROOT / 'packaging' / 'MFWSide.spec'),
    ], cwd=ROOT, env=build_env, check=True, timeout=900)
    copy_notices(output / 'licenses')
    shutil.copy2(ROOT / 'packaging' / 'RELEASE_README.md', output / 'README.md')
    probe = ROOT / '.cache' / 'windows-bundle-verification.json'
    subprocess.run([
        sys._base_executable, str(ROOT / 'tools' / 'verify_windows_build.py'),
        str(output), '--report', str(probe),
    ], cwd=ROOT, check=True, timeout=120)
    verification = json.loads(probe.read_text(encoding='utf-8'))
    if not verification.get('ok'):
        raise RuntimeError('Frozen verification failed')
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    packages = {name: version(name) for name in (*RUNTIME_PACKAGES, 'PyInstaller', 'pyinstaller-hooks-contrib')}
    info = {
        'name': 'MFWSide', 'version': app_version,
        'format': 'PyInstaller onedir', 'platform': 'windows-x86_64',
        'python': sys.version.split()[0], 'source_commit': commit,
        'created_utc': datetime.now(timezone.utc).isoformat(), 'console': args.console,
        'packages': packages, 'verification': verification,
        'external_resources_included': False, 'python_agent_runtime_included': False,
        'code_signed': False,
    }
    (output / 'BUILD-INFO.json').write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    files = bundle_files(output)
    archive = ROOT / 'dist' / f'MFWSide-{app_version}-windows-x64.zip'
    archive_bundle(output, archive)
    checksum = sha256_file(archive)
    archive.with_suffix('.zip.sha256').write_text(f'{checksum}  {archive.name}\n', encoding='ascii')
    result = {
        'version': app_version, 'source_commit': commit, 'folder': str(output.relative_to(ROOT)),
        'archive': str(archive.relative_to(ROOT)), 'archive_sha256': checksum,
        'bundle_files': len(files), 'bundle_bytes': sum(path.stat().st_size for path in files),
        'archive_bytes': archive.stat().st_size, 'verification': verification,
    }
    (ROOT / '.cache' / 'windows-build-result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
