from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from typing import Any, Protocol


MUTEX_NAME = r"Local\MoeWu.DeepSeekBalanceMonitor"
_ERROR_ALREADY_EXISTS = 183


class _WinApi(Protocol):
    def create_mutex(self, name: str) -> tuple[Any, int]: ...

    def close_handle(self, handle: Any) -> None: ...


class NamedMutex:
    def __init__(self, backend: _WinApi | None = None) -> None:
        self._backend = backend if backend is not None else _CtypesWinApi()
        self._handle: Any | None = None
        self._owns_instance = False
        self._already_exists = False

    def acquire(self) -> bool:
        if self._handle is not None:
            return self._owns_instance
        if self._already_exists:
            return False
        handle, error = self._backend.create_mutex(MUTEX_NAME)
        if not handle:
            raise OSError(error, "Unable to create monitor mutex")
        if error == _ERROR_ALREADY_EXISTS:
            self._handle = handle
            self._owns_instance = False
            self._already_exists = True
            self.close()
            return False
        self._handle = handle
        self._owns_instance = True
        return True

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._backend.close_handle(handle)
        self._handle = None
        self._owns_instance = False


def find_codex_rect() -> tuple[int, int, int, int] | None:
    """返回 CodexUsageBar 窗口的 (x, y, w, h);未找到返回 None。

    按进程路径含 "CodexUsageBar" 匹配,取可见窗口的屏幕坐标。
    """
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    user32 = ctypes.WinDLL("User32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    result: list[tuple[int, int, int, int] | None] = [None]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def callback(hwnd, _lp) -> bool:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return True
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                if "CodexUsageBar" in buf.value:
                    rect = wintypes.RECT()
                    if user32.IsWindowVisible(hwnd) and user32.GetWindowRect(
                        hwnd, ctypes.byref(rect)
                    ):
                        result[0] = (
                            rect.left,
                            rect.top,
                            rect.right - rect.left,
                            rect.bottom - rect.top,
                        )
                        return False
        finally:
            kernel32.CloseHandle(handle)
        return True

    user32.EnumWindows(callback, 0)
    return result[0]


def set_toolwindow(root: Any) -> None:
    """让 Tk 窗口不进任务栏/Alt-Tab(仿 CodexBar)。"""
    import tkinter as tk

    _GWL_EXSTYLE = -20
    _WS_EX_TOOLWINDOW = 0x00000080
    try:
        root.update_idletasks()
        hwnd = int(root.wm_frame(), 0)  # overrideredirect 窗口的真实 HWND
    except (ValueError, tk.TclError):
        return
    try:
        user32 = ctypes.WinDLL("User32.dll", use_last_error=True)
        SetWindowLong = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        SetWindowLong.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        SetWindowLong.restype = ctypes.c_ssize_t
        ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        SetWindowLong(hwnd, _GWL_EXSTYLE, ex_style | _WS_EX_TOOLWINDOW)
    except Exception:
        pass


class _CtypesWinApi:
    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows mutex is unavailable")
        self._kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
        self._kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self._kernel32.CreateMutexW.restype = wintypes.HANDLE
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL

    def create_mutex(self, name: str) -> tuple[Any, int]:
        ctypes.set_last_error(0)
        handle = self._kernel32.CreateMutexW(None, False, name)
        return handle, ctypes.get_last_error()

    def close_handle(self, handle: Any) -> None:
        if not self._kernel32.CloseHandle(handle):
            raise OSError(ctypes.get_last_error(), "Unable to close monitor mutex")
