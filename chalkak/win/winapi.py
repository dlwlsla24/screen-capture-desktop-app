"""Windows API 를 ctypes 로 직접 부른다 (추가 패키지 없이).

- 고해상도(DPI) 인식, 가상 화면 크기
- 클립보드에 그림 올리기
- 전역 단축키 (RegisterHotKey)
- 중복 실행 방지, 시작 프로그램 등록
"""

import ctypes
import io
import os
import sys
import threading
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


# ── 화면 ───────────────────────────────────────────────────────────────────


def set_dpi_aware():
    """화면 좌표를 실제 픽셀로 받는다. (안 하면 배율이 걸린 화면에서 캡처가 흐리고 좌표가 어긋난다.)"""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def virtual_screen():
    """모든 모니터를 합친 영역 (x, y, w, h). 왼쪽·위쪽 모니터가 있으면 x, y 가 음수다."""
    get = user32.GetSystemMetrics
    return get(76), get(77), get(78), get(79)


def work_area():
    """주 모니터에서 작업 표시줄을 뺀 영역 (left, top, right, bottom)."""
    area = wintypes.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(area), 0)
    return area.left, area.top, area.right, area.bottom


def pictures_dir():
    buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    try:
        if ctypes.WinDLL("shell32").SHGetFolderPathW(None, 39, None, 0, buffer) == 0 and buffer.value:  # CSIDL_MYPICTURES
            return buffer.value
    except OSError:
        pass
    return os.path.join(os.path.expanduser("~"), "Pictures")


def force_foreground(hwnd):
    """단축키로 뜬 창이 키 입력(Esc 등)을 받도록 앞으로 가져온다."""
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow(hwnd)


# ── 클립보드 ───────────────────────────────────────────────────────────────
CF_DIB = 8
GMEM_MOVEABLE = 0x0002

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT


def _global_from_bytes(data):
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        error = ctypes.WinError(ctypes.get_last_error())
        kernel32.GlobalFree(handle)
        raise error
    ctypes.memmove(pointer, data, len(data))
    kernel32.GlobalUnlock(handle)
    return handle


def copy_image(image, owner):
    """PIL 이미지를 클립보드에 올린다. 프로그램을 꺼도 내용은 남는다."""
    bmp = io.BytesIO()
    image.convert("RGB").save(bmp, "BMP")
    dib = bmp.getvalue()[14:]  # BMP 파일 머리(14바이트)를 떼면 DIB
    png = io.BytesIO()
    image.save(png, "PNG")
    for _attempt in range(10):  # 다른 프로그램이 잠깐 쥐고 있을 수 있다
        if user32.OpenClipboard(owner):
            break
        time.sleep(0.03)
    else:
        raise OSError("클립보드를 열 수 없습니다")
    try:
        if not user32.EmptyClipboard():
            raise ctypes.WinError(ctypes.get_last_error())
        for fmt, data in ((CF_DIB, dib), (user32.RegisterClipboardFormatW("PNG"), png.getvalue())):
            if not fmt:
                continue
            handle = _global_from_bytes(data)
            if not user32.SetClipboardData(fmt, handle):
                error = ctypes.WinError(ctypes.get_last_error())
                kernel32.GlobalFree(handle)
                if fmt == CF_DIB:
                    raise error
    finally:
        user32.CloseClipboard()


# ── 전역 단축키 ────────────────────────────────────────────────────────────
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 1, 2, 4, 8, 0x4000

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]


class Hotkeys(threading.Thread):
    """Receive only registered shortcuts, dispatching their actions to the UI queue."""

    def __init__(self, post):
        super().__init__(daemon=True, name="chalkak-hotkeys")
        self.post = post
        self.ready = threading.Event()
        self.labels = {}
        self.thread_id = 0

    def run(self):
        actions = {}
        self.thread_id = kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # create the thread queue
        try:
            for index, (action, key) in enumerate((("capture", "2"), ("edit", "E")), 1):
                choices = ((MOD_WIN | MOD_SHIFT, "Shift+Win"), (MOD_CONTROL | MOD_ALT, "Ctrl+Alt"))
                for modifiers, label in choices:
                    if user32.RegisterHotKey(None, index, modifiers | MOD_NOREPEAT, ord(key)):
                        actions[index] = action
                        self.labels[action] = f"{label}+{key}"
                        break
            self.ready.set()
            while actions:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam in actions:
                    self.post(actions[msg.wParam])
        except OSError as exc:
            self.post(("error", str(exc)))
        finally:
            for index in actions:
                user32.UnregisterHotKey(None, index)
            self.ready.set()

    def stop(self):
        if self.is_alive():
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self.join()


# ── 실행 관리 ──────────────────────────────────────────────────────────────
ERROR_ALREADY_EXISTS = 183
_mutex = None


def single_instance(name="SimpleChalkak.SingleInstance"):
    """이미 실행 중이면 False."""
    global _mutex
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    _mutex = kernel32.CreateMutexW(None, False, name)
    if not _mutex:
        raise ctypes.WinError(ctypes.get_last_error())
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "SimpleChalkak"


def launch_command():
    if getattr(sys, "frozen", False):  # PyInstaller 로 만든 exe
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.exists(pythonw) else sys.executable
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return f'"{exe}" "{os.path.join(root, "windows", "simple_chalkak.pyw")}"'


def autostart_enabled():
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
            return True
    except FileNotFoundError:
        return False


def set_autostart(enabled):
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass
