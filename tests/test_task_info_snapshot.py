"""Thread-safety of the ``task.info`` snapshot mechanism.

``task.info`` is a plain dict written by the TaskExecutor worker thread
(info_set/info_clear/log_* and DailyBatchTask child resets) while UI timers
and diagnostics read it.  ``BaseBD2Task`` serializes every mutator on
``_info_lock`` and readers take a copy via ``info_snapshot`` /
``task_info_snapshot``; tasks outside that hierarchy (ok-framework tasks such
as BD2TriggerTask, plain stubs) go through a best-effort unlocked fallback.
"""

import logging
import threading
import unittest
from types import SimpleNamespace

from src.tasks.BaseBD2Task import BaseBD2Task, task_info_snapshot


def _make_task(with_lock=True):
    """A BaseBD2Task shaped by ``object.__new__`` (the repo's standard stub).

    Only the attributes the info mutators touch are provided: ``info``,
    ``logger`` (ok's info_set logs non-Log/Error writes; a silenced stdlib
    logger keeps the run output readable) and optionally the per-instance
    lock; without the lock the methods exercise the shared fallback lock used
    by instances whose ``__init__`` never ran.
    """
    task = object.__new__(BaseBD2Task)
    task.info = {}
    task.logger = logging.getLogger("test_task_info_snapshot")
    task.logger.setLevel(logging.CRITICAL)
    if with_lock:
        task._info_lock = threading.RLock()
    return task


class _RacyInfoDict(dict):
    """Dict whose copy races: raising here emulates concurrent mutation."""

    def __iter__(self):
        raise RuntimeError("dictionary changed size during iteration")

    def keys(self):
        raise RuntimeError("dictionary changed size during iteration")


class TaskInfoSnapshotConcurrencyTest(unittest.TestCase):
    def test_concurrent_mutators_and_snapshots_never_raise(self):
        task = _make_task()
        errors = []

        def record_error(func):
            def wrapped():
                try:
                    func()
                except Exception as exc:  # pragma: no cover - failure signal
                    errors.append(exc)

            return wrapped

        @record_error
        def write_sets():
            for i in range(2500):
                task.info_set(f"k{i % 50}", i)

        @record_error
        def write_clears():
            for _ in range(1200):
                task.info_clear()

        @record_error
        def write_misc():
            for i in range(1200):
                task.info_incr("counter", 2)
                task.info_add("added", 3)
                task.info_add_to_list("items", i)

        threads = [
            threading.Thread(target=write_sets),
            threading.Thread(target=write_clears),
            threading.Thread(target=write_misc),
        ]
        for thread in threads:
            thread.start()

        snapshots = []
        for _ in range(400):
            snapshot = task_info_snapshot(task)
            self.assertIsInstance(snapshot, dict)
            snapshots.append(snapshot)

        for thread in threads:
            thread.join(timeout=60)
            self.assertFalse(thread.is_alive())

        self.assertEqual([], errors)
        self.assertTrue(snapshots)
        for snapshot in snapshots:
            self.assertIsInstance(snapshot, dict)

    def test_concurrent_mutators_on_lockless_task_use_fallback_lock(self):
        # object.__new__ without _info_lock: the shared fallback lock must
        # keep the mutators working instead of raising AttributeError.
        task = _make_task(with_lock=False)
        errors = []

        def write_sets():
            try:
                for i in range(1500):
                    task.info_set(f"k{i % 20}", i)
            except Exception as exc:  # pragma: no cover - failure signal
                errors.append(exc)

        thread = threading.Thread(target=write_sets)
        thread.start()
        snapshots = [task_info_snapshot(task) for _ in range(200)]
        thread.join(timeout=60)
        self.assertFalse(thread.is_alive())
        self.assertEqual([], errors)
        self.assertTrue(all(isinstance(snapshot, dict) for snapshot in snapshots))


class TaskInfoSnapshotSemanticsTest(unittest.TestCase):
    def test_info_snapshot_returns_independent_copy(self):
        task = _make_task()
        task.info_set("状态", "运行中")

        snapshot = task.info_snapshot()
        self.assertEqual({"状态": "运行中"}, snapshot)
        snapshot["状态"] = "已被修改"
        snapshot["extra"] = 1
        self.assertEqual({"状态": "运行中"}, task.info)

    def test_info_snapshot_follows_attribute_rebinding(self):
        # run_task_by_class rebinds task.info as a raw attribute; the
        # snapshot must re-read self.info inside the lock, not cache it.
        task = _make_task()
        task.info_set("旧键", 1)
        self.assertEqual({"旧键": 1}, task.info_snapshot())

        task.info = {"新键": 2}
        self.assertEqual({"新键": 2}, task.info_snapshot())

    def test_task_info_snapshot_uses_instance_info_snapshot_when_available(self):
        class _SnapshotTask:
            def info_snapshot(self):
                return {"来源": "锁内快照"}

        self.assertEqual({"来源": "锁内快照"}, task_info_snapshot(_SnapshotTask()))

    def test_task_info_snapshot_plain_stub_returns_copy(self):
        stub = SimpleNamespace(info={"状态": "完成"})
        snapshot = task_info_snapshot(stub)

        self.assertEqual({"状态": "完成"}, snapshot)
        self.assertIsNot(stub.info, snapshot)
        snapshot["状态"] = "修改不影响原对象"
        self.assertEqual({"状态": "完成"}, stub.info)

    def test_task_info_snapshot_none_info_returns_empty_dict(self):
        stub = SimpleNamespace(info=None)
        self.assertEqual({}, task_info_snapshot(stub))

    def test_task_info_snapshot_missing_info_returns_empty_dict(self):
        self.assertEqual({}, task_info_snapshot(SimpleNamespace()))

    def test_task_info_snapshot_swallows_runtime_error_from_unlocked_info(self):
        stub = SimpleNamespace(info=_RacyInfoDict({"状态": "并发修改中"}))
        self.assertEqual({}, task_info_snapshot(stub))


if __name__ == "__main__":
    unittest.main()
