"""Open Windows shortcut files without a command shell or guessing their target."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess


def is_shortcut(path):
    return Path(path).suffix.casefold() in (".lnk", ".url")


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def _libraries():
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    kernel.GetProcessId.argtypes = [wintypes.HANDLE]
    kernel.GetProcessId.restype = wintypes.DWORD
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    return shell, kernel, ole


class ShellProcess:
    """Own only a query/synchronize handle, never terminate the launched app."""
    def __init__(self, kernel, handle):
        self._kernel, self._handle = kernel, handle
        self.pid = (int(kernel.GetProcessId(handle)) or None) if handle else None
        self.executable_path = None
        if handle:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                self.executable_path = Path(buffer.value)

    def poll(self):
        if not self._handle:
            # Shell may forward to an existing app through DDE, returning no
            # process handle. Connection/window checks establish readiness.
            return None
        state = self._kernel.WaitForSingleObject(self._handle, 0)
        if state == 258:  # WAIT_TIMEOUT: process has not terminated.
            return None
        if state != 0:  # WAIT_OBJECT_0
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not self._kernel.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value

    def close(self):
        if self._handle:
            if not self._kernel.CloseHandle(self._handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None


def launch_shortcut(path, arguments=None, *, minimize=False):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("바로가기 파일을 찾을 수 없습니다.")
    if not is_shortcut(path):
        raise ValueError("바로가기 파일(.lnk, .url)을 지정하세요.")
    if path.suffix.casefold() == ".url" and arguments:
        raise ValueError("인터넷 바로가기에는 별도 실행 인수를 지정할 수 없습니다.")
    shell, kernel, ole = _libraries()
    initialized = ole.CoInitializeEx(None, 0x2 | 0x4)  # STA | DISABLE_OLE1DDE
    if initialized < 0 and initialized != -2147417850:  # RPC_E_CHANGED_MODE
        raise OSError(f"바로가기 실행을 위한 COM 초기화에 실패했습니다: {initialized:#x}")
    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100 | 0x400  # NOCLOSEPROCESS | NOASYNC | FLAG_NO_UI
    info.lpVerb, info.lpFile = "open", str(path)
    info.lpParameters = subprocess.list2cmdline(arguments) if arguments else None
    info.lpDirectory = str(path.parent)
    info.nShow = 7 if minimize else 1
    try:
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ShellProcess(kernel, info.hProcess)
        except Exception:
            if info.hProcess:
                kernel.CloseHandle(info.hProcess)
            raise
    finally:
        if initialized >= 0:
            ole.CoUninitialize()
