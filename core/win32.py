# -*- coding: utf-8 -*-
"""Win32 交互：全局热键钩子、进程/前台检测。

热键用 WH_KEYBOARD_LL 低级钩子：不需要窗口，能拿到小键盘 + / - 这类键。
钩子回调里只做「入队」，真正的活交给工作线程，避免卡住系统输入。
"""

import ctypes
import ctypes.wintypes as wt
import threading
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
WM_QUIT = 0x0012

TH32CS_SNAPPROCESS = 0x00000002


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wt.WPARAM, wt.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
kernel32.GetCurrentThreadId.restype = wt.DWORD
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
kernel32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
kernel32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]


def iter_processes():
    """遍历进程 (pid, exe_name)"""
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wt.HANDLE(-1).value:
        return
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            yield entry.th32ProcessID, entry.szExeFile
            ok = kernel32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)


def game_state(proc_name="Wow.exe", require_foreground=True):
    """返回 (状态, 说明)：ok / not_running / not_foreground"""
    name = (proc_name or "Wow.exe").lower()
    pids = {pid for pid, exe in iter_processes() if (exe or "").lower() == name}
    if not pids:
        return "not_running", "游戏未运行"
    if not require_foreground:
        return "ok", ""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return "not_foreground", "游戏未在前台"
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value in pids:
        return "ok", ""
    return "not_foreground", "游戏未在前台"


class Hotkey:
    """全局低级键盘钩子。callback(vk) 在钩子线程里被调用，必须立即返回。"""

    def __init__(self, vk, callback):
        self.vk = int(vk)
        self.callback = callback
        self._thread = None
        self._thread_id = 0
        self._hook = None
        self._proc = None
        self._ready = threading.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="hotkey")
        self._thread.start()
        self._ready.wait(timeout=2.0)
        return bool(self._hook)

    def _run(self):
        def _proc(n_code, w_param, l_param):
            try:
                if n_code >= 0 and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if kb.vkCode == self.vk:
                        # 立刻返回，真正的活在别的线程里做
                        threading.Thread(target=self.callback, args=(self.vk,),
                                         daemon=True).start()
            except Exception:
                pass
            return user32.CallNextHookEx(None, n_code, w_param, l_param)

        self._thread_id = kernel32.GetCurrentThreadId()
        self._proc = HOOKPROC(_proc)
        self._hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, None, 0)
        self._ready.set()
        if not self._hook:
            print(f"[hotkey] 安装失败: {ctypes.get_last_error()}", flush=True)
            return
        print(f"[hotkey] 已监听 VK=0x{self.vk:02X}", flush=True)

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def stop(self):
        """卸钩子并让钩子线程的 GetMessage 循环退出（否则每次重绑都漏一个线程）"""
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread_id = 0


VK_MAP = {
    "num_add": 0x6B, "num_sub": 0x6D, "num_mul": 0x6A, "num_div": 0x6F,
    "num_dec": 0x6E, "num_enter": 0x0D,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64,
    "num5": 0x65, "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pgup": 0x21, "pgdn": 0x22,
}
