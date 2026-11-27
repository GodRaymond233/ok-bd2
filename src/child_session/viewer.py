"""Native Win32 RDP viewer with a non-persistent Windows credential prompt."""

import ctypes
import os
import sys
from ctypes import wintypes

import win32con
import win32gui
from ok.child_session import win_api

RDP_CLSID = "{8B918B82-7985-4C24-89DF-C33AD2BBFBCD}"


class CredUI(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("parent", wintypes.HWND),
                ("message", wintypes.LPCWSTR), ("caption", wintypes.LPCWSTR),
                ("banner", wintypes.HANDLE)]


def credentials(parent):
    username = ctypes.create_unicode_buffer(win_api.sign_in_name(), 514)
    password = ctypes.create_unicode_buffer(514)
    save = wintypes.BOOL(False)
    info = CredUI(ctypes.sizeof(CredUI), parent,
                  "请输入 Windows 账号密码（不是 PIN）。密码只用于本次连接，不会保存。",
                  "连接 BD2 子会话", None)
    prompt = ctypes.WinDLL("credui").CredUIPromptForCredentialsW
    prompt.argtypes = [ctypes.POINTER(CredUI), wintypes.LPCWSTR, wintypes.HANDLE,
                       wintypes.DWORD, wintypes.LPWSTR, wintypes.ULONG, wintypes.LPWSTR,
                       wintypes.ULONG, ctypes.POINTER(wintypes.BOOL), wintypes.DWORD]
    result = prompt(ctypes.byref(info), "localhost", None, 0, username, len(username),
                    password, len(password), ctypes.byref(save), 0x40000 | 0x80 | 0x2 | 0x8)
    if result:
        ctypes.memset(password, 0, ctypes.sizeof(password))
        raise RuntimeError("Windows 登录已取消或不可用")
    return username, password


class Viewer:
    def __init__(self, view_only, probe=False):
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        self.events = None
        self.rdp = None
        self.child = None
        self.view_only = view_only
        window_class = win32gui.WNDCLASS()
        window_class.lpszClassName = "OkBD2ChildSessionViewer"
        window_class.lpfnWndProc = self.window_proc
        window_class.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
        win32gui.RegisterClass(window_class)
        self.window = win32gui.CreateWindow(
            window_class.lpszClassName,
            "BD2 子会话（仅查看）" if view_only else "BD2 子会话（手动操作）",
            win32con.WS_OVERLAPPEDWINDOW, win32con.CW_USEDEFAULT, win32con.CW_USEDEFAULT,
            1120, 680, 0, 0, 0, None,
        )
        if not probe:
            win32gui.ShowWindow(self.window, win32con.SW_SHOW)
        self.mstsc = comtypes.client.GetModule(os.path.join(
            os.environ["SystemRoot"], "System32", "mstscax.dll"
        ))
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        atl = ctypes.WinDLL("atl")
        atl.AtlAxWinInit()
        self.child = user32.CreateWindowExW(
            0, "AtlAxWin", RDP_CLSID,
            win32con.WS_CHILD | win32con.WS_VISIBLE | win32con.WS_CLIPCHILDREN,
            0, 0, 1100, 640, self.window, None, None, None,
        )
        if not self.child:
            raise ctypes.WinError(ctypes.get_last_error())
        unknown = ctypes.POINTER(comtypes.IUnknown)()
        result = atl.AtlAxGetControl(wintypes.HWND(self.child), ctypes.byref(unknown))
        if result:
            raise OSError("Windows RDP 控件不可用")
        self.rdp = unknown.QueryInterface(self.mstsc.IMsRdpClient9)
        self.events = comtypes.client.GetEvents(self.rdp, self, self.mstsc.IMsTscAxEvents)
        self.rdp.Server = "localhost"
        self.rdp.DesktopWidth, self.rdp.DesktopHeight = 1920, 1080
        self.rdp.AdvancedSettings9.SmartSizing = True
        self.rdp.AdvancedSettings9.EnableCredSspSupport = True
        self.rdp.QueryInterface(self.mstsc.IMsRdpExtendedSettings).Property[
            "ConnectToChildSession"
        ] = True
        if probe:
            return
        username, password = credentials(self.window)
        try:
            self.rdp.UserName = username.value
            self.rdp.AdvancedSettings9.ClearTextPassword = password.value
        finally:
            ctypes.memset(password, 0, ctypes.sizeof(password))
        self.rdp.Connect()
        win32gui.EnableWindow(self.child, not view_only)

    def OnLoginComplete(self, *_):
        self.rdp.AdvancedSettings9.ClearTextPassword = ""

    def OnDisconnected(self, *_):
        self.rdp.AdvancedSettings9.ClearTextPassword = ""
        win32gui.SetWindowText(self.window, "BD2 子会话已断开，请关闭后从设备页重新连接")

    def window_proc(self, hwnd, message, wparam, lparam):
        if message == win32con.WM_CLOSE:
            self.dispose()
            return 0
        if message == win32con.WM_SIZE and self.child:
            _, _, width, height = win32gui.GetClientRect(hwnd)
            win32gui.MoveWindow(self.child, 0, 0, width, height, True)
            return 0
        if message == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def run(self):
        try:
            win32gui.PumpMessages()
        finally:
            self.dispose()

    def dispose(self):
        from comtypes import COMError

        rdp = self.rdp
        self.events = None
        self.rdp = None
        if rdp is not None:
            try:
                rdp.AdvancedSettings9.ClearTextPassword = ""
                if rdp.Connected:
                    rdp.Disconnect()
            except COMError:
                # The ActiveX window may already have been destroyed by Windows.
                pass
        if win32gui.IsWindow(self.window):
            win32gui.DestroyWindow(self.window)


def main():
    try:
        if "--check" in sys.argv:
            viewer = Viewer(True, probe=True)
            win32gui.PostMessage(viewer.window, win32con.WM_CLOSE, 0, 0)
            viewer.run()
            print("Native RDP control, child-session property and close lifecycle: OK")
            return 0
        if "--enable" in sys.argv:
            if not win_api.enable(True):
                raise RuntimeError("Windows 不支持子会话或开启失败")
            return 0
        if not win_api.is_enabled() or win_api.in_child_session()[0]:
            raise RuntimeError("请在宿主桌面开启 Windows 子会话后连接")
        Viewer("--view-only" in sys.argv).run()
        return 0
    except Exception:
        if "--check" in sys.argv:
            print("Native RDP control check failed", file=sys.stderr)
            return 1
        win32gui.MessageBox(0, "子会话连接未完成。请检查 Windows 版本、账号密码及系统策略。",
                            "BD2 子会话", win32con.MB_OK | win32con.MB_ICONERROR)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
