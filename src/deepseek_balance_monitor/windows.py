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
