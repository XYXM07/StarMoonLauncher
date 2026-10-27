"""Apply per-instance titles only to visible game windows owned by its process."""
import os
import threading


def render_title(template, instance, account):
    title = str(template or "")
    for key, value in (("name", instance.get("name", "")), ("version", instance.get("game_version", "")),
                       ("player", account.get("name", ""))):
        title = title.replace("{" + key + "}", str(value))
    return "".join(character for character in title if ord(character) >= 32)[:256]


def set_process_title(pid, title):
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    def visit(handle, unused):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(handle):
            name = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(handle, name, len(name))
            if any(value in name.value.lower() for value in ("glfw", "lwjgl", "minecraft")):
                user.SetWindowTextW(handle, title)
        return True
    user.EnumWindows(callback_type(visit), 0)


def watch_title(process, title):
    if os.name != "nt" or not title:
        return
    def run():
        delay = threading.Event()
        while process.poll() is None:
            try:
                set_process_title(process.pid, title)
            except (OSError, AttributeError):
                pass
            delay.wait(.75)
    threading.Thread(target=run, daemon=True, name="game-window-title").start()
