"""Run the suite with per-process native crash logs and no Windows error UI."""
import argparse
import ctypes
import faulthandler
import gc
import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
_fault_stream = None


def enable_test_diagnostics():
    global _fault_stream
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetErrorMode.argtypes = [ctypes.c_uint]
        kernel32.SetErrorMode.restype = ctypes.c_uint
        # Process-local and inherited by test children; do not change the user's
        # registry, installed debugger, or the actual application's error mode.
        mode = 0x0001 | 0x0002  # FAILCRITICALERRORS | NOGPFAULTERRORBOX
        previous = kernel32.SetErrorMode(mode)
        kernel32.SetErrorMode(previous | mode)
    directory = ROOT / ".cache" / "test_diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    fault_path = directory / f"python_fault_{os.getpid()}.log"
    _fault_stream = fault_path.open("a", encoding="utf-8")
    faulthandler.enable(file=_fault_stream, all_threads=True)
    # Keep the file open through interpreter shutdown, not only through main().
    print(f"Test PID: {os.getpid()}\nNative fault log: {fault_path}", flush=True)


class CollectingResult(unittest.TextTestResult):
    def stopTest(self, test):
        super().stopTest(test)
        from PySide6.QtCore import QCoreApplication, QEvent
        if QCoreApplication.instance() is not None:
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()


class CollectingRunner(unittest.TextTestRunner):
    resultclass = CollectingResult


def run_isolated_modules(pattern, unittest_args, collect_between_tests=False):
    paths = sorted((ROOT / "UnitTest").glob(pattern))
    if not paths or any(path.parent != ROOT / "UnitTest" or not path.is_file() for path in paths):
        print("No test modules matched the local UnitTest pattern.", flush=True)
        return 1
    failures = []
    for path in paths:
        print(f"\n=== Isolated module: {path.name} ===", flush=True)
        command = [sys.executable, "-u", str(Path(__file__).resolve()),
                   "--in-process", "-p", path.name, *unittest_args]
        if collect_between_tests:
            command.append("--collect-between-tests")
        try:
            result = subprocess.run(command, cwd=ROOT, shell=False, timeout=180)
            code = result.returncode
        except subprocess.TimeoutExpired:
            print(f"Timed out: {path.name}", flush=True)
            code = 1
        if code:
            failures.append((path.name, code))
            print(f"FAILED: {path.name} (exit {code}, 0x{code & 0xFFFFFFFF:08X})", flush=True)
    print(f"\nIsolated modules: {len(paths)}, failed: {len(failures)}", flush=True)
    for name, code in failures:
        print(f"  {name}: exit {code}", flush=True)
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--collect-between-tests", action="store_true")
    parser.add_argument("--in-process", action="store_true")
    parser.add_argument("-p", "--pattern", default="test_*.py")
    options, unittest_args = parser.parse_known_args()
    enable_test_diagnostics()
    os.chdir(ROOT)
    if not options.in_process and not any(arg in ("-h", "--help") for arg in unittest_args):
        raise SystemExit(run_isolated_modules(options.pattern, unittest_args, options.collect_between_tests))
    verbosity = [] if any(arg in ("-v", "--verbose", "-q", "--quiet") for arg in unittest_args) else ["-v"]
    unittest.main(
        module=None,
        argv=["run_tests", "discover", "-s", "UnitTest", "-p", options.pattern, *verbosity, *unittest_args],
        testRunner=CollectingRunner if options.collect_between_tests else None,
    )


if __name__ == "__main__":
    main()
