"""Use owned off-screen Win32 fixtures to verify process-scoped title changes."""
import ctypes
from ctypes import wintypes
import os
import unittest
import uuid

from launcher.game_title import set_process_title


@unittest.skipUnless(os.name == "nt", "Windows game window titles")
class NativeGameTitleTests(unittest.TestCase):
    def test_only_owned_minecraft_window_is_renamed(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        callback = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        class WindowClass(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("procedure", callback), ("class_extra", ctypes.c_int),
                ("window_extra", ctypes.c_int), ("instance", wintypes.HINSTANCE), ("icon", wintypes.HICON),
                ("cursor", wintypes.HANDLE), ("background", wintypes.HBRUSH), ("menu", wintypes.LPCWSTR), ("name", wintypes.LPCWSTR)]
        user.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user.DefWindowProcW.restype = ctypes.c_ssize_t
        procedure = callback(lambda h,m,w,l: user.DefWindowProcW(h,m,w,l))
        kernel.GetModuleHandleW.restype = wintypes.HMODULE
        module = kernel.GetModuleHandleW(None)
        name = "MinecraftTitleFixture-" + uuid.uuid4().hex
        cls = WindowClass(0, procedure, 0, 0, module, None, None, None, None, name)
        user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        self.assertTrue(user.RegisterClassW(ctypes.byref(cls)))
        user.CreateWindowExW.argtypes = [wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD,
            ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.HWND,wintypes.HMENU,wintypes.HINSTANCE,wintypes.LPVOID]
        user.CreateWindowExW.restype = wintypes.HWND
        user.DestroyWindow.argtypes = [wintypes.HWND]
        user.UnregisterClassW.argtypes = [wintypes.LPCWSTR,wintypes.HINSTANCE]
        user.GetWindowTextW.argtypes = [wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]
        windows = []
        try:
            for class_name, title in ((name,"Game"),("STATIC","Unrelated")):
                handle = user.CreateWindowExW(0x08000080,class_name,title,0x90000000,-30000,-30000,1,1,None,None,module,None)
                self.assertTrue(handle)
                windows.append(handle)
            set_process_title(os.getpid(), "Fixture title")
            for handle, expected in zip(windows, ("Fixture title", "Unrelated")):
                text = ctypes.create_unicode_buffer(256)
                user.GetWindowTextW(handle, text, len(text))
                self.assertEqual(text.value, expected)
        finally:
            for handle in windows:
                user.DestroyWindow(handle)
            user.UnregisterClassW(name, module)


if __name__ == "__main__":
    unittest.main()
