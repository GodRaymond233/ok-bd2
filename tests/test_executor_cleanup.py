import os
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import numpy as np
from ok.core.events import EventBus
from ok.task.exceptions import FinishedException, TaskDisabledException
from ok.task.TaskExecutor import TaskExecutor

from src.compat import executor_cleanup
from src.tasks.run_history import RunHistoryStore, install_run_history_recorder


class ExecutorCleanupTest(unittest.TestCase):
    def make_executor(self, *, trigger=False):
        executor_cleanup.enable_executor_cleanup()
        executor = object.__new__(TaskExecutor)
        task = SimpleNamespace(
            name="cleanup test",
            running=False,
            config={},
            exit_after_task=False,
            disable=Mock(),
            info_set=Mock(),
            _app=SimpleNamespace(tr=lambda value: value),
        )
        executor.exit_event = threading.Event()
        executor.paused = False
        executor._exclusive_owner = None
        executor.current_task = None
        executor._last_frame_time = time.time()
        executor._frame = np.ones((2, 2, 3), dtype=np.uint8)
        executor._get_wake_version = Mock(return_value=0)
        executor._pop_job = Mock(return_value=None)
        executor.next_task = Mock(return_value=(task, False, trigger))
        executor._prepare_task_for_run = Mock()
        executor.reset_scene = Mock()
        executor.destroy = Mock()
        executor.trigger_tasks = [task] if trigger else []
        return executor, task

    def test_onetime_completion_and_exceptions_release_sleep_and_task_state(self):
        for exception in (
            None, "waiting for login", TaskDisabledException, FinishedException, RuntimeError,
        ):
            with self.subTest(exception=exception):
                executor, task = self.make_executor()
                task.info = {"状态": "任务完成。"}
                task.info_set.side_effect = task.info.__setitem__
                task._start_after_login = exception == "waiting for login"

                def run():
                    executor.exit_event.set()
                    if exception is not None and not task._start_after_login:
                        raise exception("stop test")

                task.run = Mock(side_effect=run)
                events = EventBus()
                with (
                    tempfile.TemporaryDirectory() as directory,
                    patch.object(executor_cleanup, "prevent_sleeping") as prevent_sleep,
                    patch.object(executor_cleanup, "communicate", events),
                    patch("ok.core.events.communicate", events),
                    patch("ok.core.events._dispatcher", None),
                    patch.object(install_run_history_recorder, "_installed", False, create=True),
                    patch.object(install_run_history_recorder, "_recorder", None, create=True),
                    patch("src.tasks.scheduler.default_store"),
                    patch.object(
                        events.task_done, "emit", wraps=events.task_done.emit,
                    ) as completed,
                ):
                    store = RunHistoryStore(os.path.join(directory, "history.json"))
                    with patch("src.tasks.run_history.default_store", return_value=store):
                        install_run_history_recorder()
                        executor.execute()
                    recent = store.recent_runs()
                    if task._start_after_login:
                        self.assertEqual([], recent)
                    else:
                        self.assertEqual(1, len(recent))
                        self.assertEqual(exception is None, recent[0]["ok"])
                        self.assertGreaterEqual(recent[0]["duration"], 0)
                        if exception in (TaskDisabledException, FinishedException):
                            self.assertEqual("已中止", recent[0]["status"])
                        elif exception is RuntimeError:
                            self.assertEqual("stop test", recent[0]["details"]["Error"])
                    self.assertEqual(1 if exception is None or task._start_after_login else 0,
                                     completed.call_count)
                self.assertEqual([call(True), call(False)], prevent_sleep.call_args_list)
                self.assertFalse(task.running)
                self.assertIsNone(executor.current_task)
                executor.destroy.assert_called_once_with()

    def test_trigger_completion_and_missing_frame_reset_task_without_marking_done(self):
        for missing_frame in (False, True):
            with self.subTest(missing_frame=missing_frame):
                executor, task = self.make_executor(trigger=True)

                def finish_cycle():
                    executor.exit_event.set()
                    return None if missing_frame else True

                task.run = Mock(side_effect=finish_cycle)
                if missing_frame:
                    executor._frame = None
                    executor.next_frame = Mock(side_effect=lambda **_: finish_cycle())
                with (
                    patch.object(executor_cleanup, "prevent_sleeping") as prevent_sleep,
                    patch.object(executor_cleanup, "communicate") as events,
                ):
                    executor.execute()
                self.assertFalse(task.running)
                self.assertIsNone(executor.current_task)
                prevent_sleep.assert_not_called()
                events.task_done.emit.assert_not_called()
                if missing_frame:
                    task.run.assert_not_called()
                else:
                    task.run.assert_called_once_with()

    def test_notification_failure_still_clears_the_current_task(self):
        executor, task = self.make_executor(trigger=True)

        def fail():
            executor.exit_event.set()
            raise RuntimeError("task failed")

        task.run = Mock(side_effect=fail)
        with patch.object(executor_cleanup, "communicate") as events:
            events.notification.emit.side_effect = RuntimeError("notification failed")
            with self.assertRaisesRegex(RuntimeError, "notification failed"):
                executor.execute()
        self.assertFalse(task.running)
        self.assertIsNone(executor.current_task)


if __name__ == "__main__":
    unittest.main()
