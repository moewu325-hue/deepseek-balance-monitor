"""Native Windows UI smoke: real window, mode size, styles, clean exit."""
import ctypes
from ctypes import wintypes
import pathlib
import os
import subprocess
import sys
import time
import tempfile
import hashlib


def run(executable):
    exe = pathlib.Path(executable).resolve()
    assert exe.is_file(), f"Native executable missing: {exe}"
    # 系统目录 API 不受 LOCALAPPDATA 覆盖影响，核对真实文件哈希。
    shell = ctypes.WinDLL("shell32")
    actual_folder = ctypes.create_unicode_buffer(260)
    assert shell.SHGetFolderPathW(None, 0x1C, None, 0, actual_folder) == 0
    actual_root = pathlib.Path(actual_folder.value) / "DeepSeekBalanceMonitor"
    def state():
        return {name: hashlib.sha256((actual_root / name).read_bytes()).hexdigest()
                if (actual_root / name).is_file() else None
                for name in ("config.json", "usage_log.json")}
    before_state = state()
    user = ctypes.WinDLL("user32", use_last_error=True)
    # 按实际像素取窗口尺寸，避免缩放导致截图裁切。
    user.SetProcessDPIAware()
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
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.SendMessageW.restype = ctypes.c_ssize_t
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
    gdi.CreateRectRgn.argtypes = [ctypes.c_int] * 4
    gdi.CreateRectRgn.restype = wintypes.HRGN
    gdi.PtInRegion.argtypes = [wintypes.HRGN, ctypes.c_int, ctypes.c_int]
    user.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN]
    system_scale = None
    for mode in ("panel", "bubble", "taskbar"):
        proc = subprocess.Popen([str(exe), f"--smoke-test={mode}", "--smoke-ms=2000"])
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
            system_scale = width / 340
        elif mode == "bubble":
            assert abs(width - height) <= 2, f"{mode}: bubble must be square"
            assert abs(width - round(108 * system_scale)) <= 1, f"{mode}: expected enlarged 108-DIP diameter, got {width}px"
        else:
            assert width >= height * 3, f"{mode}: bad taskbar geometry"
        screen = user.GetDC(hwnd)
        memory = gdi.CreateCompatibleDC(screen)
        bitmap = gdi.CreateCompatibleBitmap(screen, width, height)
        previous = gdi.SelectObject(memory, bitmap)
        try:
            # 窗口可见早于首帧就绪，按像素条件限时等待。
            render_deadline = time.monotonic() + .5
            colors = set()
            printed = False
            while True:
                printed = bool(user.PrintWindow(hwnd, memory, 2))
                if printed:
                    colors = {gdi.GetPixel(memory, x, y)
                              for x in range(5, width - 5, 3)
                              for y in range(5, height - 5, 3)}
                if printed and len(colors) > 2 and 0x002A170F in colors:
                    break
                if time.monotonic() >= render_deadline:
                    break
                time.sleep(.01)
            detail = (f"pid={proc.pid} hwnd={hwnd:#x} size={width}x{height} "
                      f"colors={len(colors)} values={sorted(colors)[:8]}")
            assert printed, f"{mode}: PrintWindow failed ({detail})"
            assert len(colors) > 2, f"{mode}: blank render ({detail})"
            assert 0x002A170F in colors, f"{mode}: missing navy background ({detail})"
            logical_width = {"panel": 340, "bubble": 72, "taskbar": 184}[mode]
            scale = width / logical_width
            def pixel(x, y):
                return gdi.GetPixel(memory, round(x * scale), round(y * scale))
            def sample(peak, elapsed):
                assert user.SendMessageW(hwnd, 0x8002, peak, elapsed) == 1, f"{mode}: smoke control rejected"
                assert user.PrintWindow(hwnd, memory, 2), f"{mode}: sample render failed"
                return tuple(pixel(x, y) for y in range(7, 43) for x in range(7, 66)) if mode == "bubble" else ()
            lamp = {"panel": (206, 26), "bubble": (22, 64), "taskbar": (48, 34)}[mode]
            sample(1, 0)
            assert pixel(*lamp) == 0x007165F8, f"{mode}: peak red lamp missing"
            sample(0, 0)
            assert pixel(*lamp) == 0x0098D64C, f"{mode}: off-peak green lamp missing"
            for peak, elapsed in ((2, 0), (0, -1), (0, 60001)):
                assert user.SendMessageW(hwnd, 0x8002, peak, elapsed) == 0, f"{mode}: invalid smoke control accepted"
            assert user.PrintWindow(hwnd, memory, 2)
            assert pixel(*lamp) == 0x0098D64C, f"{mode}: invalid control changed peak state"
            if mode == "bubble":
                sample(0, 4000)
                region = gdi.CreateRectRgn(0, 0, 0, 0)
                try:
                    assert user.GetWindowRgn(hwnd, region), "bubble: missing circular window region"
                    assert not gdi.PtInRegion(region, 0, 0), "bubble: square corner exposed"
                    assert gdi.PtInRegion(region, width // 2, height // 2), "bubble: center clipped"
                finally:
                    gdi.DeleteObject(region)
                assert pixel(54, 13) == 0x00DBEFE1, "bubble: moon missing"
                assert pixel(12, 27) == 0x00442F14, "bubble: distant mountains missing"
                assert len({pixel(36, y) for y in (5, 10, 20, 25)}) >= 3, "bubble: sky depth missing"
                icon_pixels = sum(pixel(x, y) == 0x00FE6B4D for y in range(7, 43) for x in range(7, 66))
                assert icon_pixels >= 20, "bubble: original DeepSeek icon pixels missing"
                amount = tuple(pixel(x, y) for y in range(45, 60) for x in range(8, 65))
                frames = [sample(0, elapsed) for elapsed in (0, 2000, 6000, 12000, 12800, 13600)]
                assert len(set(frames)) == 6, "bubble: swim/jump samples must differ"
                assert tuple(pixel(x, y) for y in range(45, 60) for x in range(8, 65)) == amount, "bubble: animation covered the amount"
                assert frames[0] == sample(0, 16000), "bubble: timeline must repeat"
                after = wintypes.RECT()
                user.GetWindowRect(hwnd, ctypes.byref(after))
                assert (after.left, after.top) == (rect.left, rect.top), "bubble: animation moved the window"
                for command, diameter in enumerate((72, 90, 108, 132, 160), 120):
                    user.SendMessageW(hwnd, 0x0111, command, 0)
                    user.GetWindowRect(hwnd, ctypes.byref(after))
                    actual = after.right - after.left
                    assert abs(actual - round(diameter * system_scale)) <= 1, f"bubble: size setting {diameter} failed, got {actual}px"
                    assert actual == after.bottom - after.top, "bubble: resized view is not square"
                user.SendMessageW(hwnd, 0x0111, 122, 0)
                for command, source in ((119, 0), (125, 0), (120 | (1 << 16), 0), (120, 1)):
                    user.SendMessageW(hwnd, 0x0111, command, source)
                    user.GetWindowRect(hwnd, ctypes.byref(after))
                    assert after.right - after.left == width, "bubble: invalid size command changed geometry"
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
    assert state() == before_state, "Smoke changed actual user configuration/history"
    print("PASS actual user data hashes unchanged")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "build/native/DeepSeekBalanceMonitor.exe")
