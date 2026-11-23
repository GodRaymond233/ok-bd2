import threading
import unittest
from unittest import mock

from src.utils import topmost_alert


class TopmostAlertCallTest(unittest.TestCase):

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


if __name__ == "__main__":
    unittest.main()
