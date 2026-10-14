"""Report transient native windows while an isolated worker command runs."""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes


EVENT_OBJECT_SHOW = 0x8002
OBJID_WINDOW = 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WINEVENT_OUTOFCONTEXT = 0
WINEVENT_SKIPOWNPROCESS = 2


def main() -> int:
    if os.name != "nt":
        raise SystemExit("This observer requires Windows")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("Pass a command after --")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
    )
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    callback_type = ctypes.WINFUNCTYPE(
        None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
        wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
    )
    user32.SetWinEventHook.argtypes = (
        wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, callback_type,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    )
    user32.SetWinEventHook.restype = wintypes.HANDLE
    user32.UnhookWinEvent.argtypes = (wintypes.HANDLE,)
    user32.PeekMessageW.argtypes = (
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT,
        wintypes.UINT, wintypes.UINT,
    )

    events: list[tuple[str, int, str, str]] = []

    def on_show(_hook, _event, hwnd, object_id, child_id, _thread, _time):
        if object_id != OBJID_WINDOW or child_id != 0 or not user32.IsWindowVisible(hwnd):
            return
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return
        try:
            executable = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(executable))
            if not kernel32.QueryFullProcessImageNameW(
                handle, 0, executable, ctypes.byref(length)
            ):
                return
        finally:
            kernel32.CloseHandle(handle)
        name = os.path.basename(executable.value).lower()
        if name not in {"tesseract.exe", "pdfinfo.exe", "pdftoppm.exe", "pdftocairo.exe"}:
            return
        window_class = ctypes.create_unicode_buffer(256)
        title = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(hwnd, window_class, len(window_class))
        user32.GetWindowTextW(hwnd, title, len(title))
        events.append((name, pid.value, window_class.value, title.value))
        print(f"WINDOW {name} pid={pid.value} class={window_class.value} title={title.value!r}", flush=True)

    callback = callback_type(on_show)
    hook = user32.SetWinEventHook(
        EVENT_OBJECT_SHOW, EVENT_OBJECT_SHOW, None, callback,
        0, 0, WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS,
    )
    if not hook:
        raise OSError(ctypes.get_last_error(), "SetWinEventHook failed")
    try:
        process = subprocess.Popen(command)
        message = wintypes.MSG()
        while process.poll() is None:
            while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
                pass
            time.sleep(0.01)
        while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            pass
        print(f"Observed {len(events)} vendor-tool window events", flush=True)
        return process.returncode
    finally:
        user32.UnhookWinEvent(hook)


if __name__ == "__main__":
    sys.exit(main())
