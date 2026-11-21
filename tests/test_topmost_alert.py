import io
import threading
import unittest
from contextlib import redirect_stderr
from unittest import mock

from src.utils import topmost_alert


class TopmostAlertFlagsTest(unittest.TestCase):

    def test_flags_mark_box_as_topmost(self):
        """弹框必须压在最上层：警告图标 + 抢前台 + 始终置顶。"""

        flags = topmost_alert.messagebox_flags()
        self.assertEqual(flags & 0x30, 0x30)
        self.assertEqual(flags & 0x10000, 0x10000)
        self.assertEqual(flags & 0x40000, 0x40000)


class TopmostAlertCallTest(unittest.TestCase):

    def test_blocking_shows_one_box(self):
        calls = []
        with mock.patch.object(topmost_alert, "_messagebox", lambda *a: calls.append(a)):
            self.assertTrue(topmost_alert.show_topmost_alert("卡住了", blocking=True))

        self.assertEqual(len(calls), 1)
        message, title, flags = calls[0]
        self.assertEqual(message, "卡住了")
        self.assertEqual(title, topmost_alert.DEFAULT_TITLE)
        self.assertEqual(flags, topmost_alert.messagebox_flags())

    def test_non_blocking_returns_immediately(self):
        """任务线程不能被弹框卡住：弹框在守护线程里等玩家点确定。"""

        called = threading.Event()
        seen = []

        def fake_messagebox(message, title, flags):
            seen.append((message, title))
            called.set()

        with mock.patch.object(topmost_alert, "_messagebox", fake_messagebox):
            self.assertTrue(topmost_alert.show_topmost_alert("需要手动处理"))
            self.assertTrue(called.wait(2.0), "弹框线程没有执行")

        self.assertEqual(seen, [("需要手动处理", topmost_alert.DEFAULT_TITLE)])

    def test_messagebox_failure_does_not_raise(self):
        def boom(*_args):
            raise OSError("no desktop")

        with mock.patch.object(topmost_alert, "_messagebox", boom):
            with redirect_stderr(io.StringIO()):
                self.assertTrue(topmost_alert.show_topmost_alert("卡住了", blocking=True))

    def test_empty_message_shows_nothing(self):
        calls = []
        with mock.patch.object(topmost_alert, "_messagebox", lambda *a: calls.append(a)):
            self.assertFalse(topmost_alert.show_topmost_alert(""))
        self.assertEqual(calls, [])

    def test_task_failure_alert_names_task_and_error(self):
        """任务异常停止时，置顶框要写清是哪个任务、什么错误。"""

        calls = []
        shown = threading.Event()

        def fake_messagebox(message, title, flags):
            calls.append((message, title, flags))
            shown.set()

        with mock.patch.object(topmost_alert, "_messagebox", fake_messagebox):
            self.assertTrue(
                topmost_alert.show_task_failure_alert(
                    "主线快速探索",
                    "等待输入桌面恢复超时，本次操作尚未执行",
                )
            )
            self.assertTrue(shown.wait(2.0), "失败弹框线程没有执行")

        self.assertEqual(len(calls), 1)
        message, title, _flags = calls[0]
        self.assertEqual(title, topmost_alert.FAILURE_TITLE)
        self.assertIn("主线快速探索", message)
        self.assertIn("等待输入桌面恢复超时", message)
        self.assertIn("重新启动任务", message)

    def test_task_failure_alert_without_error_text(self):
        calls = []
        shown = threading.Event()

        def fake_messagebox(message, title, flags):
            calls.append((message, title, flags))
            shown.set()

        with mock.patch.object(topmost_alert, "_messagebox", fake_messagebox):
            topmost_alert.show_task_failure_alert("主线快速探索", "")
            self.assertTrue(shown.wait(2.0), "失败弹框线程没有执行")
        self.assertIn("未知错误", calls[0][0])

    def test_non_windows_falls_back_to_stderr(self):
        calls = []
        buffer = io.StringIO()
        with mock.patch.object(topmost_alert.os, "name", "posix"), \
                mock.patch.object(topmost_alert, "_messagebox", lambda *a: calls.append(a)), \
                redirect_stderr(buffer):
            self.assertFalse(topmost_alert.show_topmost_alert("卡住了"))

        self.assertEqual(calls, [])
        self.assertIn("卡住了", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
