"""Mouse operations executed exclusively inside the child session."""

import time

import win32gui


class MouseInput:
    def __init__(self, log=None):
        from pynput import mouse

        self.mouse = mouse.Controller()
        self.buttons = mouse.Button

    def bring_to_front(self, hwnd):
        if not hwnd:
            return False
        if win32gui.GetForegroundWindow() != hwnd:
            try:
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                return False
        return win32gui.GetForegroundWindow() == hwnd

    def _position(self, hwnd, x, y):
        width, height = win32gui.GetClientRect(hwnd)[2:]
        if not (0 <= x < width and 0 <= y < height):
            raise ValueError("鼠标目标超出子会话游戏客户区")
        return win32gui.ClientToScreen(hwnd, (int(x), int(y)))

    def _land(self, hwnd, x, y):
        point = self._position(hwnd, x, y)
        if not self.bring_to_front(hwnd):
            raise RuntimeError("子会话游戏未取得前台，请在查看器中确认游戏窗口")
        self.mouse.position = point
        time.sleep(0.08)
        if win32gui.GetForegroundWindow() != hwnd:
            raise RuntimeError("子会话前台窗口已变化，取消鼠标操作")

    def click(self, hwnd, x, y, key="left", down_time=0.05):
        if key not in ("left", "right", "middle"):
            raise ValueError("不支持的鼠标按钮")
        self._land(hwnd, x, y)
        button = getattr(self.buttons, key)
        self.mouse.press(button)
        try:
            time.sleep(min(max(float(down_time), 0.05), 2))
        finally:
            self.mouse.release(button)

    def scroll(self, hwnd, x, y, amount):
        self._land(hwnd, x, y)
        self.mouse.scroll(0, max(-100, min(100, int(amount))))

    def swipe(self, hwnd, from_x, from_y, to_x, to_y, duration=0.5, settle_time=0):
        start = self._position(hwnd, from_x, from_y)
        end = self._position(hwnd, to_x, to_y)
        duration = min(max(float(duration), 0.05), 5)
        self._land(hwnd, from_x, from_y)
        self.mouse.press(self.buttons.left)
        try:
            steps = max(1, round(duration / 0.02))
            for step in range(1, steps + 1):
                if win32gui.GetForegroundWindow() != hwnd:
                    raise RuntimeError("子会话拖拽期间游戏失去前台")
                self.mouse.position = tuple(
                    round(a + (b - a) * step / steps) for a, b in zip(start, end)
                )
                time.sleep(duration / steps)
            time.sleep(min(max(float(settle_time), 0), 2))
        finally:
            self.mouse.release(self.buttons.left)
