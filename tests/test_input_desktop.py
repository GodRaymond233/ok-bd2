import unittest
from unittest import mock

from src.interaction import BD2Interaction as interaction_module
from src.interaction.BD2Interaction import BD2Interaction


class FakeTime:
    def __init__(self, start=0.0):
        self.now = start
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make_interaction():
    """Skip __init__: wait_for_input only touches _activate_required."""

    interaction = object.__new__(BD2Interaction)
    interaction._activate_required = False
    return interaction


class WaitForInputTest(unittest.TestCase):

    def setUp(self):
        self.clock = FakeTime()
        self.time_patch = mock.patch.object(interaction_module, "time", self.clock)
        self.time_patch.start()
        self.addCleanup(self.time_patch.stop)

    def test_healthy_desktop_returns_position(self):
        """可用时只探测一次：读光标 + 写回同一位置。"""

        with mock.patch.object(
            interaction_module, "GetCursorPos", return_value=(100, 200),
        ), mock.patch.object(
            interaction_module, "SetCursorPos",
        ) as set_cursor:
            position = make_interaction().wait_for_input()

        self.assertEqual(position, (100, 200))
        set_cursor.assert_called_once_with((100, 200))
        self.assertEqual(self.clock.sleeps, [])

    def test_set_cursor_pos_failure_waits_instead_of_killing_task(self):
        """锁屏时 GetCursorPos 仍然成功、SetCursorPos 被拒绝，要先等待再重试。"""

        set_cursor = mock.Mock(
            side_effect=[
                OSError("(0, 'SetCursorPos', 'No error message is available')"),
                OSError("(0, 'SetCursorPos', 'No error message is available')"),
                None,
            ]
        )
        messages = []
        interaction = make_interaction()

        with mock.patch.object(
            interaction_module, "GetCursorPos", return_value=(10, 20),
        ), mock.patch.object(interaction_module, "SetCursorPos", set_cursor):
            position = interaction.wait_for_input(
                wait=self.clock.sleep, status=messages.append,
            )

        self.assertEqual(position, (10, 20))
        self.assertEqual(set_cursor.call_count, 3)
        self.assertEqual(
            messages,
            [
                "输入桌面暂不可用（可能为锁屏/UAC），等待恢复，最长30秒",
                "输入桌面已恢复，继续尚未执行的操作",
            ],
        )
        self.assertEqual(len(self.clock.sleeps), 2)

    def test_gives_up_with_clear_timeout(self):
        """一直不可用时抛出可读错误，由任务层弹置顶框，而不是崩溃在点击里。"""

        messages = []
        get_cursor = mock.Mock(
            side_effect=OSError("(5, 'GetCursorPos', 'Access is denied.')")
        )

        with mock.patch.object(
            interaction_module, "GetCursorPos", get_cursor,
        ), mock.patch.object(interaction_module, "SetCursorPos") as set_cursor:
            with self.assertRaises(TimeoutError) as ctx:
                make_interaction().wait_for_input(
                    wait=self.clock.sleep, status=messages.append,
                )

        self.assertIn("等待输入桌面恢复超时", str(ctx.exception))
        set_cursor.assert_not_called()
        self.assertEqual(
            messages, ["输入桌面暂不可用（可能为锁屏/UAC），等待恢复，最长30秒"],
        )


class PlaceCursorTest(unittest.TestCase):

    def test_retries_then_succeeds(self):
        set_cursor = mock.Mock(side_effect=[OSError("lost"), None])
        with mock.patch.object(
            interaction_module, "SetCursorPos", set_cursor,
        ), mock.patch.object(interaction_module.time, "sleep"):
            make_interaction()._place_cursor((50, 60))

        self.assertEqual(set_cursor.call_count, 2)

    def test_raises_after_all_attempts(self):
        with mock.patch.object(
            interaction_module,
            "SetCursorPos",
            side_effect=OSError("lost"),
        ), mock.patch.object(interaction_module.time, "sleep"):
            with self.assertRaises(TimeoutError) as ctx:
                make_interaction()._place_cursor((50, 60))

        self.assertIn("本次点击尚未发送", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
