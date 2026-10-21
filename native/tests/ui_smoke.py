"""Native Windows UI smoke: real window, mode size, styles, clean exit."""
import ctypes
from ctypes import wintypes
import pathlib
import os
import subprocess
import sys
import time
import tempfile


def run(executable):
    exe = pathlib.Path(executable).resolve()
    assert exe.is_file(), f"Native executable missing: {exe}"
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetWindowLongW.restype = ctypes.c_long
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetDC.argtypes = [wintypes.HWND]
    user.GetDC.restype = wintypes.HDC
    user.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    gdi.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi.CreateCompatibleDC.restype = wintypes.HDC
    gdi.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
    gdi.SelectObject.restype = wintypes.HANDLE
    gdi.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi.GetPixel.restype = wintypes.DWORD
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi.DeleteDC.argtypes = [wintypes.HDC]
    for mode in ("panel", "bubble", "taskbar"):
        proc = subprocess.Popen([str(exe), f"--smoke-test={mode}"])
        found = []
        @callback
        def visit(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == proc.pid and user.IsWindowVisible(hwnd):
                rect = wintypes.RECT()
                user.GetWindowRect(hwnd, ctypes.byref(rect))
                found.append((hwnd, rect))
            return True
        deadline = time.monotonic() + 0.9
        while not found and time.monotonic() < deadline:
            user.EnumWindows(visit, 0)
            time.sleep(.025)
        assert found, f"{mode}: no visible native window"
        hwnd, rect = found[0]
        exstyle = user.GetWindowLongW(hwnd, -20)
        assert exstyle & 0x80, f"{mode}: missing WS_EX_TOOLWINDOW"
        assert not exstyle & 0x8, f"{mode}: must not be topmost"
        width, height = rect.right - rect.left, rect.bottom - rect.top
        assert width > 0 and height > 0, f"{mode}: empty window"
        if mode == "panel":
            assert width > height, f"{mode}: bad panel geometry"
        elif mode == "bubble":
            assert abs(width - height) <= 2, f"{mode}: bubble must be square"
        else:
            assert width >= height * 3, f"{mode}: bad taskbar geometry"
        screen = user.GetDC(hwnd)
        memory = gdi.CreateCompatibleDC(screen)
        bitmap = gdi.CreateCompatibleBitmap(screen, width, height)
        previous = gdi.SelectObject(memory, bitmap)
        try:
            assert user.PrintWindow(hwnd, memory, 2), f"{mode}: PrintWindow failed"
            colors = {gdi.GetPixel(memory, x, y)
                      for x in range(5, width - 5, 3)
                      for y in range(5, height - 5, 3)}
            assert len(colors) > 2, f"{mode}: blank render"
            assert 0x002A170F in colors, f"{mode}: missing navy background"
        finally:
            gdi.SelectObject(memory, previous)
            gdi.DeleteObject(bitmap)
            gdi.DeleteDC(memory)
            user.ReleaseDC(hwnd, screen)
        assert proc.wait(timeout=5) == 0, f"{mode}: unclean exit"
        print(f"PASS {mode}: {width}x{height}, toolwindow, clean exit")
    assert subprocess.run([str(exe), "--version"], timeout=5).returncode == 0
    for arg in ("--smoke-test=wrong", "--smoke-test=panel --smoke-ms=0",
                "--smoke-test=panel --smoke-ms=10001", "--smoke-test=panel --smoke-ms=abc"):
        assert subprocess.run([str(exe), *arg.split()], timeout=5).returncode == 2
    with tempfile.TemporaryDirectory(prefix="deepseek-smoke-") as sandbox:
        environment = dict(os.environ, LOCALAPPDATA=sandbox, DEEPSEEK_API_KEY="smoke-invalid-key")
        assert subprocess.run([str(exe), "--smoke-test=panel", "--smoke-ms=100"],
                              env=environment, timeout=5).returncode == 0
        assert not list(pathlib.Path(sandbox).iterdir()), "Smoke wrote persistent state"
    print("PASS invalid modes/durations and smoke isolation")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "build/native/DeepSeekBalanceMonitor.exe")
