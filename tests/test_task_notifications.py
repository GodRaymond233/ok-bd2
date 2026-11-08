import unittest
from types import SimpleNamespace

from src.tasks.task_notifications import (
    log_task_completion,
    suppress_task_completion_notifications,
)


class TaskCompletionNotificationTest(unittest.TestCase):
    def make_task(self):
        calls = []
        task = SimpleNamespace(
            log_info=lambda message, notify=False: calls.append((message, notify))
        )
        return task, calls

    def test_standalone_completion_notifies(self):
        task, calls = self.make_task()

        log_task_completion(task, "任务完成")

        self.assertEqual([("任务完成", True)], calls)

    def test_suppressed_completion_only_logs_and_restores_task(self):
        task, calls = self.make_task()

        with suppress_task_completion_notifications(task):
            log_task_completion(task, "子任务完成")

        self.assertEqual([("子任务完成", False)], calls)
        self.assertFalse(
            hasattr(task, "_completion_notification_suppression_depth")
        )


if __name__ == "__main__":
    unittest.main()
